import pandas as pd

data = {
    "month": ["2024-01", "2024-01", "2024-02", "2024-02", "2024-03"],
    "product": ["apple", "banana", "apple", "banana", "apple"],
    "sales": [100, 80, 120, 90, 150]
}

df = pd.DataFrame(data)
result = df.groupby("month")["sales"].sum()

print(result)