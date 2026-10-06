"""OpenTelemetry span mapping for an execution record.

One workflow maps to one trace. Spans carry only compact, low-cardinality,
indexed attributes: tool name and version, an argument-value digest for correlation, result
status, authorization verdict, and latency. Redacted argument payloads and
outputs stay in the execution record, referenced by record_id; they are not
copied into span attributes.

The default exporter is an in-memory exporter so tests need no collector. An
OTLP exporter to the optional Docker Compose collector is available when the
environment requests it; that import is lazy so the core dependency stays the
SDK alone.

Note: OpenTelemetry's GenAI semantic conventions (gen_ai.*) are still evolving,
so this module keeps its own arh.* namespace as authoritative and maps onto
gen_ai.* only where stable. Specific gen_ai.* attribute names are marked
[VERIFY SOURCE] in the handbook rather than hard-coded as settled here.
"""
from __future__ import annotations

import os

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ..harness.record import ExecutionRecord

SERVICE_NAME = "agentic-reliability-harness"


def build_tracer_provider(exporter=None) -> TracerProvider:
    """Create a provider. Defaults to an in-memory exporter for tests.

    Set ARH_OTLP_ENDPOINT to export to the Compose collector instead; the OTLP
    exporter is imported lazily so it is not a hard dependency.
    """
    resource = Resource.create({"service.name": SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    if exporter is None:
        endpoint = os.environ.get("ARH_OTLP_ENDPOINT")
        if endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
                OTLPSpanExporter,
            )
            exporter = OTLPSpanExporter(endpoint=endpoint)
        else:
            exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    provider._arh_exporter = exporter  # convenience handle for tests
    return provider


def emit_record_spans(record: ExecutionRecord, provider: TracerProvider) -> None:
    """Emit a span tree for one execution record.

    workflow (root)
      model_call        one per observable decision (step)
      tool_call         one per gateway call
        authorization   the PDP verdict for that call, when present
    """
    tracer = provider.get_tracer("arh.telemetry")

    with tracer.start_as_current_span("workflow") as root:
        root.set_attribute("arh.workflow.record_id", record.record_id)
        root.set_attribute("arh.scenario.id", record.scenario_id)
        root.set_attribute("arh.adapter.mode", record.adapter_mode)
        root.set_attribute("arh.termination.reason", record.termination_reason)
        root.set_attribute("arh.prompt.version", record.reproducibility.get("prompt_version", ""))
        root.set_attribute("arh.model.config", record.reproducibility.get("model_configuration", ""))
        root.set_attribute("arh.identity.caller", record.identity.get("caller", ""))
        root.set_attribute("arh.model.calls", record.model_calls)
        root.set_attribute("arh.latency.e2e_ms", float(record.latencies.get("e2e_ms", 0.0)))

        for step in record.steps:
            if step.index < 0:
                continue  # synthetic init step is not a model decision
            with tracer.start_as_current_span("model_call") as m:
                m.set_attribute("arh.step.index", step.index)
                m.set_attribute("arh.step.kind", step.kind)
                m.set_attribute("arh.prompt.version", record.reproducibility.get("prompt_version", ""))

        authz_by_index = {a.index: a for a in record.authorization_decisions}
        for tc in record.tool_calls:
            with tracer.start_as_current_span("tool_call") as t:
                t.set_attribute("arh.tool.name", tc.tool)
                t.set_attribute("arh.tool.version", tc.tool_version or "")
                t.set_attribute("arh.tool.args_hash", tc.arguments_hash or "")
                t.set_attribute("arh.tool.result_status", tc.status)
                t.set_attribute("arh.tool.reason", tc.reason)
                azr = authz_by_index.get(tc.index)
                if azr is not None:
                    with tracer.start_as_current_span("authorization") as a:
                        a.set_attribute("arh.authz.decision", azr.decision)
                        a.set_attribute("arh.authz.reason", azr.reason)
                        a.set_attribute("arh.authz.policy", azr.policy or "")
                        if azr.approval_result:
                            a.set_attribute("arh.approval.result", azr.approval_result)
