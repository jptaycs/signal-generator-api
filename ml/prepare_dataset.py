import pandas as pd

# --- Load the predictor signal history (from your bot) ---
predictor_df = pd.read_csv("../data/signals_log.csv", parse_dates=["timestamp"])

# --- Load Pocket Option trade history (from Excel file) ---
po_df = pd.read_excel("../data/pocket_option_history.xlsx", parse_dates=["Open time", "Close time"])

# --- Clean asset names (e.g., "USD/CHF" vs "USDCHF") ---
po_df["Asset"] = po_df["Asset"].astype(str).str.replace(" ", "").str.strip()
predictor_df["pair"] = predictor_df["pair"].astype(str).str.replace(" ", "").str.strip()


# --- Rename predictor column to match Pocket Option ---
predictor_df.rename(columns={"pair": "Asset"}, inplace=True)

# --- Ensure both timestamps are timezone-naive ---
po_df["Open time"] = pd.to_datetime(po_df["Open time"]).dt.tz_localize(None)
predictor_df["timestamp"] = pd.to_datetime(predictor_df["timestamp"]).dt.tz_localize(None)

print(f"po_df['Open time'] dtype: {po_df['Open time'].dtype}, predictor_df['timestamp'] dtype: {predictor_df['timestamp'].dtype}")

# --- Merge predictions with real trades within ±10 minutes ---
merged = pd.merge_asof(
    po_df.sort_values("Open time"),
    predictor_df.sort_values("timestamp"),
    by="Asset",
    left_on="Open time",
    right_on="timestamp",
    direction="nearest",
    tolerance=pd.Timedelta("60min")
)

# --- Save merged dataset for ML training ---
merged.to_csv("../data/merged_history.csv", index=False)
print("✅ Merged dataset saved as data/merged_history.csv")
print(f"✅ Total merged rows: {len(merged)}")