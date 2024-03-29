import time
import numpy as np
import json
import pandas as pd
import yfinance as yf
from datetime import datetime
from pymongo import MongoClient
from progress.bar import ChargingBar
import os
from stock_analysis.utils import clean_dataframe

start_run = time.time()


def determine_quarter_col(date):
    year = date.year
    if (
        datetime(day=1, month=11, year=year - 1)
        <= date
        <= datetime(day=31, month=3, year=year)
    ):
        col = f"Q3 {str(year - 1)}"
    elif (
        datetime(day=1, month=4, year=year)
        <= date
        <= datetime(day=30, month=4, year=year)
    ):
        col = f"Q4 {str(year - 1)}"
    elif (
        datetime(day=1, month=5, year=year)
        <= date
        <= datetime(day=31, month=7, year=year)
    ):
        col = f"Q1 {str(year)}"
    elif (
        datetime(day=1, month=8, year=year)
        <= date
        <= datetime(day=31, month=10, year=year)
    ):
        col = f"Q2 {str(year)}"
    elif (
        datetime(day=1, month=11, year=year)
        <= date
        <= datetime(day=31, month=3, year=year + 1)
    ):
        col = f"Q3 {str(year)}"
    elif (
        datetime(day=1, month=4, year=year + 1)
        <= date
        <= datetime(day=30, month=4, year=year + 1)
    ):
        col = f"Q4 {str(year)}"

    return col


def calculate_final_percentage(initial_value, profit_loss_percentages):
    percentage_changes = np.array(profit_loss_percentages) / 100.0
    final_value = initial_value * np.prod(1 + percentage_changes)
    percentage_change = ((final_value - initial_value) / initial_value) * 100
    return percentage_change


# MongoDB
cluster = MongoClient(os.environ["MONGODB_URI"])
db_stockbit_data = cluster["stockbit_data"]
collection_quarterly = db_stockbit_data["quarterly"]

# Dates
start_date = datetime(year=2008, month=5, day=1)
end_date = datetime.now()

# Directory
dir = "./src/stock_analysis/backtest_strategy"
date_str = "202307120"

# List of stocks
date_stocks_list = "20230416"
index_name = "LQ45"
stocks = pd.read_csv(
    f"./src/stock_analysis/static/{date_stocks_list}_stocks_list_{index_name}.csv",
    index_col=0,
).index.to_list()

for stock in stocks:
    # Print log started backtest
    print(f"Started running backtest: {stock}")

    # Create folder
    if not os.path.exists(f"{dir}/data/{date_str}"):
        os.mkdir(f"{dir}/data/{date_str}")

    if not os.path.exists(f"{dir}/data/{date_str}/{stock}"):
        os.mkdir(f"{dir}/data/{date_str}/{stock}")

    timeframes = [0.25, 0.5, 0.75, 1, 3, 5]
    strat_types = ["-1+1", "-1+2", "-2+1", "-2+2", "-2+0", "+0+2"]

    data_quarterly = collection_quarterly.find_one({"stock_code": stock})
    if not data_quarterly:
        print(f"Data quarterly not available for {stock}")

    yf_stock = yf.Ticker(f"{stock}.JK")

    df_is = pd.DataFrame(data_quarterly["income_statement"]).loc[
        ["Total Revenue", "Net Income Attributable To", "Share Outstanding"]
    ]
    df_is = clean_dataframe(df_is).loc[:, ::-1]
    df_is.loc["Net Income Attributable To"] = (
        df_is.loc["Net Income Attributable To"].rolling(4).sum()
    )
    df_bs = pd.DataFrame(data_quarterly["balance_sheet"]).loc[
        ["Total Equity", "Share Outstanding"]
    ]
    df_bs = clean_dataframe(df_bs).loc[:, ::-1]

    # Get price data from yfinance
    stock_price = (
        yf_stock.history(start=start_date, end=end_date, auto_adjust=False)
        .Close.reset_index()
        .set_index("Date")
    )
    stock_price.index = stock_price.tz_localize(None).index

    pe = pd.DataFrame()
    pbv = pd.DataFrame()
    ps = pd.DataFrame()

    for ix, values in stock_price.iterrows():
        try:
            col = determine_quarter_col(ix)

            pe.loc[ix, "pe"] = values.Close / (
                float(df_is.loc["Net Income Attributable To", col])
                / (float(df_is.loc["Share Outstanding"][-1]))
            )
            pbv.loc[ix, "pbv"] = values.Close / (
                float(df_bs.loc["Total Equity", col])
                / (float(df_is.loc["Share Outstanding"][-1]))
            )
            ps.loc[ix, "ps"] = values.Close / (
                float(df_is.loc["Total Revenue", col])
                / (float(df_is.loc["Share Outstanding"][-1]))
            )

            if pe.loc[ix, "pe"] < 0:
                pe.loc[ix, "pe"] = np.nan

            if pbv.loc[ix, "pbv"] < 0:
                pbv.loc[ix, "pbv"] = np.nan

            if ps.loc[ix, "ps"] < 0:
                ps.loc[ix, "ps"] = np.nan

        except:
            pe.loc[ix, "pe"] = np.nan
            pbv.loc[ix, "pbv"] = np.nan
            ps.loc[ix, "ps"] = np.nan

    pe.to_csv(f"{dir}/data/{date_str}/{stock}/pe_ratio.json")
    pbv.to_csv(f"{dir}/data/{date_str}/{stock}/pbv_ratio.json")
    ps.to_csv(f"{dir}/data/{date_str}/{stock}/ps_ratio.json")

    results = {}
    for multiple in ["pe", "pbv", "ps"]:
        results[f"{multiple}"] = {}
        if multiple == "pe":
            df = pe
        elif multiple == "pbv":
            df = pbv
        elif multiple == "ps":
            df = ps

        for strat_type in strat_types:
            results[f"{multiple}"][f"{strat_type}"] = {}
            for tf in timeframes:
                results[f"{multiple}"][f"{strat_type}"][f"{tf}"] = {}
                days = round(365 * tf)
                df[f"mean_+0_stdev_{tf}y"] = (
                    df[f"{multiple}"].rolling(f"{str(days)}d").mean()
                )
                df[f"stdev_{tf}y"] = df[f"{multiple}"].rolling(f"{str(days)}d").std()
                df[f"mean_+2_stdev_{tf}y"] = (
                    df[f"mean_+0_stdev_{tf}y"] + 2 * df[f"stdev_{tf}y"]
                )
                df[f"mean_-2_stdev_{tf}y"] = (
                    df[f"mean_+0_stdev_{tf}y"] - 2 * df[f"stdev_{tf}y"]
                )
                df[f"mean_+1_stdev_{tf}y"] = (
                    df[f"mean_+0_stdev_{tf}y"] + 1 * df[f"stdev_{tf}y"]
                )
                df[f"mean_-1_stdev_{tf}y"] = (
                    df[f"mean_+0_stdev_{tf}y"] - 1 * df[f"stdev_{tf}y"]
                )

                # Determine timestamp buy/sell
                bought = False
                timestamp_buy = {}
                timestamp_sell = {}

                low_limit = strat_type[:2]
                high_limit = strat_type[2:]

                i = 0

                for ix, row in df.iterrows():
                    if (
                        row[f"{multiple}"] < row[f"mean_{low_limit}_stdev_{tf}y"]
                        and bought == False
                    ):
                        bought = True
                        timestamp_buy[i] = [ix]

                    # elif (
                    #     row[f"{multiple}"] < row[f"mean_{low_limit}_stdev_{tf}y"]
                    #     and stock_price.loc[ix]["Close"]
                    #     <= stock_price.loc[timestamp_buy[i][-1]]["Close"]
                    #     and bought == True
                    # ):
                    #     bought = True
                    #     timestamp_buy[i].append(ix)
                    elif (
                        row[f"{multiple}"] > row[f"mean_{high_limit}_stdev_{tf}y"]
                        and bought == True
                    ):
                        bought = False
                        timestamp_sell[i] = ix
                        i += 1

                profits = []

                for (i, buys), (j, sell) in zip(
                    timestamp_buy.items(),
                    timestamp_sell.items(),
                ):
                    profit = {}
                    profit["timestamp_buy"] = [str(buy) for buy in buys]
                    profit["timestamp_sell"] = str(sell)

                    buy_index = [
                        stock_price.iloc[
                            stock_price.index.get_indexer([buy], method="nearest")
                        ].index[0]
                        for buy in buys
                    ]
                    sell_index = stock_price.iloc[
                        stock_price.index.get_indexer([sell], method="nearest")
                    ].index[0]
                    profit["price_buy"] = stock_price.loc[buy_index].values.mean()
                    profit["price_sell"] = stock_price.loc[sell_index].values[0]
                    profit["profit_pct"] = (
                        (profit["price_sell"] - profit["price_buy"])
                        / profit["price_buy"]
                        * 100
                    )

                    profit[f"{multiple}_buy"] = df.loc[
                        buy_index, f"{multiple}"
                    ].values.mean()
                    profit[f"{multiple}_mean_{tf}y_buy"] = df.loc[
                        buy_index, f"mean_+0_stdev_{tf}y"
                    ].values.mean()
                    profit[f"{multiple}_mean_+1_stdev_{tf}y_buy"] = df.loc[
                        buy_index, f"mean_+1_stdev_{tf}y"
                    ].values.mean()
                    profit[f"{multiple}_mean_-1_stdev_{tf}y_buy"] = df.loc[
                        buy_index, f"mean_-1_stdev_{tf}y"
                    ].values.mean()
                    profit[f"{multiple}_mean_+2_stdev_{tf}y_buy"] = df.loc[
                        buy_index, f"mean_+2_stdev_{tf}y"
                    ].values.mean()
                    profit[f"{multiple}_mean_-2_stdev_{tf}y_buy"] = df.loc[
                        buy_index, f"mean_-2_stdev_{tf}y"
                    ].values.mean()

                    profit[f"{multiple}_sell"] = df.loc[sell_index, f"{multiple}"]
                    profit[f"{multiple}_mean_{tf}y_sell"] = df.loc[
                        sell_index, f"mean_+0_stdev_{tf}y"
                    ]
                    profit[f"{multiple}_mean_+1_stdev_{tf}y_sell"] = df.loc[
                        sell_index, f"mean_+1_stdev_{tf}y"
                    ]
                    profit[f"{multiple}_mean_-1_stdev_{tf}y_sell"] = df.loc[
                        sell_index, f"mean_-1_stdev_{tf}y"
                    ]
                    profit[f"{multiple}_mean_+2_stdev_{tf}y_sell"] = df.loc[
                        sell_index, f"mean_+2_stdev_{tf}y"
                    ]
                    profit[f"{multiple}_mean_-2_stdev_{tf}y_sell"] = df.loc[
                        sell_index, f"mean_-2_stdev_{tf}y"
                    ]

                    profits.append(profit)

                profit_pct_comp = 1
                for profit in profits:
                    profit_pct_comp = profit_pct_comp * (1 + profit["profit_pct"] / 100)
                profit_pct_comp = (profit_pct_comp - 1) * 100

                if len(profits) > 0:
                    first_multiple_timestamp = df.dropna().index[0]
                    last_multiple_timestamp = df.dropna().index[-1]
                    timerange = (
                        last_multiple_timestamp - first_multiple_timestamp
                    ).days
                    annual_compound_rate = (
                        np.power(1 + profit_pct_comp / 100, 1 / (timerange / 365)) - 1
                    ) * 100

                    results[f"{multiple}"][f"{strat_type}"][f"{tf}"] = {
                        "profit_pct_comp": profit_pct_comp,
                        "annual_profit": annual_compound_rate,
                        "first_multiple_timestamp": str(first_multiple_timestamp),
                        "last_multiple_timestamp": str(last_multiple_timestamp),
                        "profits": profits,
                    }
                else:
                    results[f"{multiple}"][f"{strat_type}"][f"{tf}"] = {}

    # Print log finished backtest
    elapsed_time = time.time() - start_run
    print(f"Finished running backtest: {stock}")
    print(f"Elapsed time: {elapsed_time} seconds")

    # Save file
    with open(f"{dir}/data/{date_str}/{stock}/backtest_data.json", "w") as outfile:
        json.dump(results, outfile)

    test = 1
