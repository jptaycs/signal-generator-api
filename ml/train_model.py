import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report
import joblib
import os

# --- Load dataset ---
data_path = os.path.join(os.path.dirname(__file__), "../data/merged_history.csv")
df = pd.read_csv(data_path)

# --- Clean and prepare ---
df["result"] = df["Profit"].apply(lambda x: 1 if x > 0 else 0)

features = [
    "rsi_value", "ema20_value", "ema50_value", "macd_value", "stoch_k_value",
    "bb_high_value", "bb_low_value", "cci_value", "adx_value",
    "willr_value", "atr_value", "obv_value"
]

# Keep only numeric and fill missing data
df = df[features + ["result"]]
initial_rows = len(df)
df = df.fillna(df.mean(numeric_only=True))
print(f"🧹 Cleaned dataset: kept {len(df)} rows out of {initial_rows} (filled missing values)")

X = df[features]
y = df["result"]

# --- Split ---
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42
)

# --- Train model ---
model = RandomForestClassifier(n_estimators=200, random_state=42)
model.fit(X_train, y_train)

# --- Evaluate ---
y_pred = model.predict(X_test)
print("✅ Accuracy:", accuracy_score(y_test, y_pred))
print("\n📊 Classification Report:")
print(classification_report(y_test, y_pred))

# --- Feature importance ---
importance = pd.DataFrame({
    "Indicator": features,
    "Importance": model.feature_importances_
}).sort_values("Importance", ascending=False)

print("\n🔥 Indicator Importance:")
print(importance)

# --- Save model ---
save_path = os.path.join(os.path.dirname(__file__), "../data/indicator_winrate_model.pkl")
joblib.dump(model, save_path)
print(f"\n💾 Model saved at: {save_path}")