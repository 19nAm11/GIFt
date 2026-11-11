"""Deprecated path: the Streamlit app now lives in apps/streamlit_app.py.

`streamlit run app.py` keeps working. Streamlit re-runs this script on every interaction,
so run the real app each time instead of importing it once.
Remove this file once open branches have rebased.
"""
import runpy

runpy.run_module("apps.streamlit_app", run_name="__main__")
