import os

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def provider(service: str) -> TracerProvider:
    result = TracerProvider(resource=Resource.create({"service.name": service}))
    result.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=os.getenv(
                    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
                    "http://127.0.0.1:4318/v1/traces",
                ),
                timeout=3,
            ),
            schedule_delay_millis=500,
        )
    )
    return result
