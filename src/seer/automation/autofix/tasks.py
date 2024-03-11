import dataclasses
import logging
from typing import Any

import sentry_sdk
from requests import HTTPError

from celery_app.app import app as celery_app
from seer.automation.autofix.autofix import Autofix
from seer.automation.autofix.event_manager import AutofixEventManager
from seer.automation.autofix.models import (
    AutofixCompleteArgs,
    AutofixContinuation,
    AutofixGroupState,
    AutofixRequest,
    AutofixStatus,
    AutofixStepUpdateArgs,
)
from seer.automation.models import InitializationError
from seer.automation.state import LocalMemoryState, State
from seer.rpc import RpcClient, SentryRpcClient

logger = logging.getLogger("autofix")


@dataclasses.dataclass
class ContinuationState(LocalMemoryState[AutofixContinuation]):
    rpc_client: RpcClient = dataclasses.field(default_factory=SentryRpcClient)

    def reload_state_from_sentry(self) -> bool:
        try:
            self.val = self.val.model_copy(
                update=AutofixGroupState.model_validate(
                    self.rpc_client.call("get_autofix_state", issue_id=self.val.request.issue.id)
                ).model_dump()
            )
            return True
        except HTTPError as e:
            if e.response.status_code == 404:
                return False
            raise e

    def set(self, continuation: AutofixContinuation):
        if continuation.status in {AutofixStatus.ERROR, AutofixStatus.COMPLETED}:
            self.rpc_client.call(
                "on_autofix_complete",
                **AutofixCompleteArgs(
                    issue_id=continuation.request.issue.id,
                    status=continuation.status,
                    steps=continuation.steps,
                    fix=continuation.fix,
                ).model_dump(mode="json"),
            )
        else:
            self.rpc_client.call(
                "on_autofix_step_update",
                **AutofixStepUpdateArgs(
                    issue_id=continuation.request.issue.id,
                    status=continuation.status,
                    steps=continuation.steps,
                ).model_dump(mode="json"),
            )
        super().set(continuation)


@celery_app.task(time_limit=60 * 60 * 5)  # 5 hour task timeout
def run_autofix(
    request_data: dict[str, Any],
    autofix_group_state: dict[str, Any] | None = None,
):
    state = ContinuationState(
        val=AutofixContinuation(request=AutofixRequest.model_validate(request_data))
    )
    request = AutofixRequest(**request_data)
    event_manager = AutofixEventManager(state)

    try:
        if not state.reload_state_from_sentry():
            raise InitializationError("Group no longer exists")

        cur = state.get()

        # Process has no further work.
        if cur.status in AutofixStatus.terminal():
            return

        if cur.request.has_timed_out:
            raise InitializationError("Timeout while dealing with autofix request.")

        with sentry_sdk.start_span(
            op="seer.automation.autofix",
            description="Run autofix on an issue within celery task",
        ):
            autofix = Autofix(request, event_manager)
            autofix.run()
    except InitializationError as e:
        event_manager.send_autofix_complete(None)
        sentry_sdk.capture_exception(e)
        raise e
