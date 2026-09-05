import pandas as pd
import requests
from bs4 import BeautifulSoup
import time
import math
from tqdm import tqdm
from tkinter import messagebox, Tk

# -------------------------------
# FILE PATHS
# -------------------------------
INPUT_FILE = "pids.xlsx"
BATCH_SIZE = 500

# -------------------------------
# HEADERS
# -------------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

# -------------------------------
# SCRAPE FUNCTION
# -------------------------------
def get_product_details(pid):
    url = f"https://www.flipkart.com/product/p/itm?pid={pid}"
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)

        if response.status_code != 200:
            return "Not Found", "N/A", "N/A"

        soup = BeautifulSoup(response.text, "html.parser")

        name_tag = soup.find("span", {"class": "v1zwn21k v1zwn26 _1psv1zeb9 _1psv1ze0"}) or \
                   soup.find("h1")
        product_name = name_tag.text.strip() if name_tag else "Not Found"

        rating_tag = soup.find("div", {"class": "css-146c3p1"})
        rating = rating_tag.text.strip() if rating_tag else "N/A"

        review_tag = soup.find("div", {"class": "css-146c3p1"})
        reviews = review_tag.text.strip() if review_tag else "N/A"

        return product_name, rating, reviews

    except Exception:
        return "Error", "Error", "Error"

# -------------------------------
# READ INPUT
# -------------------------------
df = pd.read_excel(INPUT_FILE)

if "FSN_ID" not in df.columns:
    print("Error: FSN_ID column missing")
    exit()

df["FSN_ID"] = df["FSN_ID"].astype(str)

# -------------------------------
# BATCH PROCESSING
# -------------------------------
total_rows = len(df)
num_batches = math.ceil(total_rows / BATCH_SIZE)

print(f"Total rows: {total_rows}")
print(f"Processing in {num_batches} batches...\n")

for batch_num in range(num_batches):
    
    start = batch_num * BATCH_SIZE
    end = start + BATCH_SIZE
    
    batch_df = df.iloc[start:end].copy()
    
    print(f"\nProcessing Batch {batch_num + 1} (Rows {start} to {end})")

    product_names = []
    ratings = []
    reviews_list = []

    for pid in tqdm(batch_df["FSN_ID"], desc=f"Batch {batch_num + 1}", unit="product"):
        print(f"Fetching: {pid}")
        
        name, rating, reviews = get_product_details(pid)
        
        product_names.append(name)
        ratings.append(rating)
        reviews_list.append(reviews)
        
        time.sleep(2)

    batch_df["Product_Name"] = product_names
    batch_df["Rating"] = ratings
    batch_df["Reviews"] = reviews_list

    output_file = f"output_batch_{batch_num + 1}.xlsx"
    batch_df.to_excel(output_file, index=False)

    print(f"✅ Saved: {output_file}")

print("\n🎉 All batches completed!")

root = Tk()
root.withdraw()
messagebox.showinfo("Finished", "All batches completed successfully!")
root.destroy()