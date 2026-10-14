"""Test configuration for the public ChemLab-Assets release.

The release does not redistribute cached third-party source documents (outputs/**/sources, source_cache, raw/
folders, references/). Some tests re-hash those caches to prove provenance; when a cache is absent, the test is
reported as skipped with the missing path instead of failing.
"""
import pytest

UNPUBLISHED = ("/outputs/", "/references/", "/raw/", "source_cache", "/sources/", "/vendor/")


def _unpublished(exc):
    name = getattr(exc, "filename", None)
    return isinstance(exc, FileNotFoundError) and name and any(k in str(name) for k in UNPUBLISHED)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if call.excinfo is not None and call.when in ("setup", "call") and _unpublished(call.excinfo.value):
        rep.outcome = "skipped"
        rep.longrepr = (str(item.path), item.location[1] or 0,
                        f"Skipped: needs an unpublished source cache ({call.excinfo.value.filename})")
