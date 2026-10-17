import pandas as pd
import requests
import io

print("Loading US Cities database for instant offline matching...")
# Accurate open-source US cities database with Lat/Lon
url = "https://raw.githubusercontent.com/kelvins/US-Cities-Database/main/csv/us_cities.csv"
response = requests.get(url, timeout=15)
us_cities = pd.read_csv(io.StringIO(response.text))

# Clean city names and state codes
us_cities['city_clean'] = us_cities['CITY'].astype(str).str.strip().str.upper()
us_cities['state_clean'] = us_cities['STATE_CODE'].astype(str).str.strip().str.upper()

# Load assignment CSV
df = pd.read_csv('fuel-prices-for-be-assessment.csv')
df.columns = [c.strip() for c in df.columns]

df['city_clean'] = df['City'].astype(str).str.strip().str.upper()
df['state_clean'] = df['State'].astype(str).str.strip().str.upper()

# Fast merge on City and State
merged = df.merge(
    us_cities[['city_clean', 'state_clean', 'LATITUDE', 'LONGITUDE']].drop_duplicates(subset=['city_clean', 'state_clean']),
    on=['city_clean', 'state_clean'],
    how='left'
)

# Rename to lat, lon
merged['lat'] = merged['LATITUDE']
merged['lon'] = merged['LONGITUDE']

# Drop temporary columns
clean_df = merged.drop(columns=['city_clean', 'state_clean', 'LATITUDE', 'LONGITUDE'])

# Drop rows that couldn't be matched
clean_df = clean_df.dropna(subset=['lat', 'lon'])
clean_df.to_csv('fuel_stations_with_coords.csv', index=False)

print(f"Success! {len(clean_df)} stations mapped with accurate coordinates to fuel_stations_with_coords.csv")