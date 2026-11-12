"""Config flow entry point.

Home Assistant looks for the flow in this module; the implementation is in
the ``flow`` package.
"""
from .flow import InimPrimeConfigFlow, InimPrimeOptionsFlowHandler

__all__ = ["InimPrimeConfigFlow", "InimPrimeOptionsFlowHandler"]
