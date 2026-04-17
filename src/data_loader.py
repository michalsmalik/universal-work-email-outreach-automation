"""Spreadsheet loading helpers.

This module provides typed helpers to load Streamlit-uploaded CSV/XLSX files
into pandas DataFrames.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def load_uploaded_file(uploaded_file: Any) -> pd.DataFrame:
    if uploaded_file is None:
        raise ValueError("No uploaded file provided.")

    file_name = uploaded_file.name.lower()

    if file_name.endswith(".xlsx"):
        return pd.read_excel(uploaded_file, engine="openpyxl")
    if file_name.endswith(".csv"):
        return pd.read_csv(uploaded_file)

    raise ValueError("Only .xlsx and .csv files are supported.")
