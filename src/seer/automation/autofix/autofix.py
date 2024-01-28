import logging
import os
import xml.etree.ElementTree as ET

import torch
from github import Auth

from seer.automation.agent.agent import GptAgent, Message, Usage
from seer.automation.autofix.agent_context import AgentContext
from seer.automation.autofix.prompts import coding_prompt, planning_prompt
from seer.automation.autofix.tools import BaseTools, CodeActionTools
from seer.automation.autofix.types import (
    AutofixAgentsOutput,
    AutofixOutput,
    AutofixRequest,
    PlanningOutput,
)

# TODO: Remove this when we stop locking it to a repo
REPO_OWNER = "getsentry"

logger = logging.getLogger("autofix")
logger.addHandler(logging.FileHandler("./autofix.log"))
logger.addHandler(logging.StreamHandler())


class Autofix:
    def __init__(self, request: AutofixRequest):
        self.request = request
        self.device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")

        app_id = os.environ.get("GITHUB_APP_ID")
        private_key = os.environ.get("GITHUB_PRIVATE_KEY")

        if app_id is None or private_key is None:
            raise ValueError("GITHUB_APP_ID or GITHUB_PRIVATE_KEY is not set")

        self.github_auth = Auth.AppAuth(app_id, private_key=private_key)

    def run(self) -> AutofixOutput | None:
        logger.info(f"Beginning autofix for issue {self.request.issue.id}")

        github_repo_name = os.environ.get("GITHUB_REPO_NAME")
        self.context = AgentContext(
            repo_owner=REPO_OWNER,
            repo_name=github_repo_name if github_repo_name else "sentry-mirror-suggested-fix",
            ref=None if self.request.base_commit_sha else "heads/master",
            base_sha=self.request.base_commit_sha,
            model="gpt-4-1106-preview",
            github_auth=self.github_auth,
            gpu_device=self.device,
        )

        logger.info(f"Running planning for issue {self.request.issue.id}")
        planning_output = self._run_planning_agent()
        if planning_output is None:
            logger.warning(f"Planning agent did not return a valid output")
            return None

        logger.info(f"Running coding for issue {self.request.issue.id}")
        coding_output, coding_usage = self._run_coding_agent(self.context.base_sha, planning_output)

        model_dump = planning_output.model_dump()
        model_dump.pop("usage", None)  # Remove the existing usage key if present
        combined_output = AutofixAgentsOutput(
            **model_dump,
            changes=list(coding_output.values()),
            usage=Usage(
                prompt_tokens=planning_output.usage.prompt_tokens + coding_usage.prompt_tokens,
                completion_tokens=planning_output.usage.completion_tokens
                + coding_usage.completion_tokens,
                total_tokens=planning_output.usage.total_tokens + coding_usage.total_tokens,
            ),
        )

        pr = self._create_pr(combined_output)

        self.context.cleanup()

        output = AutofixOutput(
            title=planning_output.title,
            description=planning_output.description,
            plan=planning_output.plan,
            usage=planning_output.usage,
            pr_url=pr.html_url,
            repo_name=self.context.repo.full_name,
            pr_number=pr.number,
        )

        return output

    def _create_pr(self, combined_output: AutofixAgentsOutput):
        branch_ref = self.context.create_branch_from_changes(
            pr_title=combined_output.title,
            file_changes=combined_output.changes,
            base_commit_sha=self.context.base_sha,
        )

        return self.context.create_pr_from_branch(
            branch_ref, combined_output, self.request.issue.id
        )

    def _run_planning_agent(self) -> PlanningOutput | None:
        planning_agent_tools = BaseTools(self.context)

        planning_agent = GptAgent(
            tools=planning_agent_tools.get_tools(),
            memory=[
                Message(
                    role="system",
                    content=planning_prompt.format(
                        err_msg=self.request.issue.title,
                        stack_str=self.request.issue.events[-1].build_stacktrace(),
                    ),
                )
            ],
        )

        additional_context_str = (
            (self.request.additional_context + "\n\n") if self.request.additional_context else ""
        )
        planning_response = planning_agent.run(
            # TODO: Remove this and also find how to address mismatches in the stack trace path and the actual filepaths
            f"{additional_context_str}Note: instead of ./app, the correct directory is static/app/..."
        )

        parsed_output = ET.fromstring(f"<response>{planning_response}</response>")

        try:
            title_element = parsed_output.find("title")
            description_element = parsed_output.find("description")
            plan_element = parsed_output.find("plan")

            title = title_element.text if title_element is not None else None
            description = description_element.text if description_element is not None else None
            plan = plan_element.text if plan_element is not None else None

            if title is None or description is None or plan is None:
                logger.warning(
                    f"Planning response does not contain a title, description, or plan: {planning_response}"
                )
                return None

            return PlanningOutput(
                title=title, description=description, plan=plan, usage=planning_agent.usage
            )
        except AttributeError as e:
            logger.warning(f"Planning response does not contain a title, description, or plan: {e}")
            return None

    def _run_coding_agent(self, base_sha: str, planning_output: PlanningOutput):
        code_action_tools = CodeActionTools(
            self.context,
            base_sha=base_sha,
            verbose=True,
        )
        action_agent = GptAgent(
            tools=code_action_tools.get_tools(),
            memory=[
                Message(
                    role="system",
                    content=coding_prompt.format(
                        err_msg=self.request.issue.title,
                        stack_str=self.request.issue.events[-1].build_stacktrace(),
                        comment=self.request.additional_context,
                    ),
                )
            ],
        )

        action_agent.run(planning_output.plan)

        return code_action_tools.file_changes, action_agent.usage