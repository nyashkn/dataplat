"""Sources: read external systems into polars frames.

Rules: every column comes back as a string with an explicit schema (no type inference); no lake access
(``dataplat lint`` DPA005); never list storage (compute the file name, or read a manifest); select only
the columns you need, so restricted fields never leave the source.
"""
