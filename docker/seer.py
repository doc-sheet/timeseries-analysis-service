import sentry_sdk
import time
import os

from flask import Flask, request
from sentry_sdk.integrations.flask import FlaskIntegration
import pandas as pd
import numpy as np
from datetime import datetime

from prophet_detector import ProphetDetector, ProphetParams

sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN"),
    integrations=[FlaskIntegration()],
    traces_sample_rate=1.0,
)
app = Flask(__name__)

MODEL_PARAMS = ProphetParams(
    interval_width=0.95,
    changepoint_prior_scale=0.01,
    weekly_seasonality=14,
    daily_seasonality=False,
    uncertainty_samples=None,
)
model_initialized = False
detector = ProphetDetector(MODEL_PARAMS)
model_initialized = True


@app.route("/anomaly/predict", methods=["POST"])
def predict():
    data = request.get_json()
    start, end = data["start"], data["end"]
    granularity = data["granularity"]
    ads_context = {
        "start": start,
        "end": end,
        "low_threshold": detector.low_threshold,
        "high_threshold": detector.high_threshold,
    }
    snuba_context = {
        "granularity": granularity
    }
    sentry_sdk.set_context("snuba_query", snuba_context)
    sentry_sdk.set_context("anomaly_detection_params", ads_context)

    with sentry_sdk.start_span(
        op="data.preprocess", description="Preprocess data to prepare for anomaly detection"
    ) as span:
        detector.pre_process_data(pd.DataFrame(data["data"]), start, end)

    with sentry_sdk.start_span(
        op="model.train", description="Train forecasting model"
    ) as span:
        detector.fit()

    with sentry_sdk.start_span(op="model.predict", description="Generate predictions") as span:
        fcst = detector.predict()

    with sentry_sdk.start_span(
        op="model.confidence", description="Generate confidence intervals"
    ) as span:
        detector.add_prophet_uncertainty(fcst)

    with sentry_sdk.start_span(
        op="data.anomaly.scores", description="Generate anomaly scores using forecast"
    ) as span:
        fcst = detector.scale_scores(fcst)

    with sentry_sdk.start_span(
        op="data.format", description="Format data for frontend"
    ) as span:
        output = process_output(fcst, granularity)

    return output


@app.route("/health/live", methods=["GET"])
def health_check():
    return "", 200


@app.route("/health/ready", methods=["GET"])
def ready_check():
    if model_initialized:
        return "", 200
    else:
        return "Model not initialized", 503


def aggregate_anomalies(data, granularity):
    """
    Group consecutive anomalies together into single
    records (with expanded start/end times)

    Attributes:
    data: the input dataframe (with anomalies added)
    granularity: data granularity (in seconds)

    Returns:
    results: list of dictionaries containing combined anomalies
        start: when anomaly started
        end: when anomaly ended
        confidence: anomaly confidence
        received: actual count for metric
        expected: expected count for metric (from yhat)
        id: id/label for each anomaly
    """
    anomalies = []
    last_score = None
    anomaly_index = -1
    sum_expected, sum_actual = 0, 0
    for ds_time, score, y, yhat in data.itertuples(index=False):
        if score == last_score:
            anomalies[anomaly_index]["end"] = int(ds_time + granularity)
            anomalies[anomaly_index]["received"] += round(y, 5)
            anomalies[anomaly_index]["expected"] += round(yhat, 5)
        else:
            sum_expected = yhat
            sum_actua = y
            anomaly_index += 1
            anomalies.append(
                {
                    "start": int(ds_time),
                    "end": int(ds_time + granularity),
                    "confidence": score,
                    "received": round(y, 5),
                    "expected": round(yhat, 5),
                    "id": anomaly_index,
                }
            )
        last_score = score

    return anomalies


def process_output(data, granularity):
    """
    Format data for frontend

    Attributes:
    data: the input dataframe (with anomalies added)
    granularity: data granularity (seconds)

    Returns:
    results: dictionary containing
        y: input timeseries
        yhat_upper: upper confidence bound
        yhat_lower: lower confidence bound
        anomalies: all detected anomalies
    """

    def convert_ts(ts, value_col):
        """
        Format a timeseries for the frontend
        """
        data = zip(
            list(map(int, ts["ds"])), [[{"count": round(x, 5)}] for x in list(ts[value_col])]
        )
        start = int(ts["ds"].iloc[0])
        end = int(ts["ds"].iloc[-1])
        return {"data": list(data), "start": start, "end": end}

    data["ds"] = data["ds"].astype(np.int64) * 1e-9
    anomalies_data = data[~data["anomalies"].isna()][["ds", "anomalies", "y", "yhat"]]
    anomalies = []
    if len(anomalies_data) > 0:
        anomalies = aggregate_anomalies(anomalies_data, granularity)

    results = {
        "y": convert_ts(data, "y"),
        "yhat_upper": convert_ts(data, "yhat_upper"),
        "yhat_lower": convert_ts(data, "yhat_lower"),
        "anomalies": anomalies,
    }
    return results
