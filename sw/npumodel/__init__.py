"""Model descriptor schema, config ingestion and derived workload metrics (Phase 1)."""
from .schema import (SCHEMA_VERSION, DTYPE_BYTES, validate, expand_layers, count_params,
                     expected_weight_bytes, kv_bytes, load, dump)
