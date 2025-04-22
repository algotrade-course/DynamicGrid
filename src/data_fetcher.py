import pandas as pd
import numpy as np
import os
import psycopg
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def load_query_from_file(filepath="data/query.txt"):
    """
    Load SQL query from a text file
    """
    try:
        with open(filepath, 'r') as file:
            query = file.read()
        return query
    except Exception as e:
        print(f"Error loading query from {filepath}: {e}")
        return None

def fetch_vn30_data(query=None):
    """
    Fetch VN30F futures data from the database
    
    Parameters:
    ----------
    query : str, optional
        SQL query to execute. If None, the query will be loaded from data/query.txt
    """
    print("Fetching data from database...")
    try:
        # If no query is provided, load it from the file
        if query is None:
            query = load_query_from_file()
            if query is None:
                print("Failed to load query from file.")
                return None
        
        with psycopg.connect(
            host=os.getenv('DB_HOST'),
            port=int(os.getenv('DB_PORT')),  # Convert port to integer
            dbname=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD')
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                result = cur.fetchall()
                
                if not result:
                    print("Query returned no results.")
                    return None
                    
                timestamps = [row[0] for row in result]
                prices = [float(row[1]) for row in result]
                df = pd.Series(prices, index=pd.to_datetime(timestamps))
                print(f"Successfully fetched {len(df)} data points.")
                return df
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None

def save_data_to_file(data, filename="data/vn30_data.csv"):
    """
    Save time series data to a CSV file
    """
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(filename) or '.', exist_ok=True)
        data.to_csv(filename)
        print(f"Data saved to {filename}")
        return True
    except Exception as e:
        print(f"Error saving data to {filename}: {e}")
        return False

def load_data_from_file(filename="data/vn30_data.csv"):
    """
    Load time series data from a CSV file
    """
    try:
        if os.path.exists(filename):
            data = pd.read_csv(filename, index_col=0, parse_dates=True).squeeze("columns")
            if data.empty:
                print(f"File {filename} exists but contains no data.")
                return None
            print(f"Data loaded from {filename}: {len(data)} data points.")
            return data
        else:
            print(f"File {filename} does not exist.")
            return None
    except Exception as e:
        print(f"Error loading data from {filename}: {e}")
        return None

def prepare_data(config, mode="in_sample"):
    """
    Prepare data for either in-sample or out-sample based on config
    """
    if mode == "in_sample":
        file_path = config['data']['in_sample_file']
        start_date = pd.to_datetime(config['data']['in_sample']['start_date'])
        end_date = pd.to_datetime(config['data']['in_sample']['end_date'])
    else:
        file_path = config['data']['out_sample_file']
        start_date = pd.to_datetime(config['data']['out_sample']['start_date'])
        end_date = pd.to_datetime(config['data']['out_sample']['end_date'])
    
    prices = None
    
    # Try to fetch data if configured
    if config['data']['fetch_data']:
        prices = fetch_vn30_data()
        if prices is not None and config['data']['save_fetched_data']:
            # Always save fetched data to "data/vn30_data.csv" instead of file_path
            save_data_to_file(prices, "data/vn30_data.csv")
    
    # If fetching failed or wasn't configured, try to load from file
    if prices is None:
        prices = load_data_from_file(file_path)
        
        # If specified file doesn't exist, try the default file as fallback
        if prices is None and file_path != "data/vn30_data.csv":
            print(f"Trying to load from default file data/vn30_data.csv as fallback...")
            prices = load_data_from_file("data/vn30_data.csv")

    # Filter by date range if we have data
    if prices is not None:
        # Make a copy to avoid potential warnings about modifying a slice
        original_count = len(prices)
        prices = prices[(prices.index >= start_date) & (prices.index <= end_date)].copy()
        print(f"Filtered data from {original_count} to {len(prices)} points based on date range.")
        
        if prices.empty:
            print(f"Warning: No data points in the specified date range {start_date} to {end_date}.")
            return None
    else:
        print("Failed to obtain price data from any source.")
        
    return prices 