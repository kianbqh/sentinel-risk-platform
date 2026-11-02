import math


def vector_literal(values):
    if len(values) != 384 or any(type(v) not in (float, int) or not math.isfinite(v) for v in values):
        raise ValueError("A finite 384-dimensional vector is required")
    if not any(values):
        raise ValueError("Zero vectors are not usable for cosine distance")
    return "[" + ",".join(repr(float(v)) for v in values) + "]"
