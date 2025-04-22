import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import time, datetime
import os

def calculate_atr(prices, period=50, window=None, last_valid_atr=1.0):
    """
    Calculate Average True Range
    """
    high_low = prices.diff().abs()
    if window is not None:
        atr = high_low.tail(window).dropna().mean()
        return atr if not pd.isna(atr) and len(high_low.tail(window).dropna()) >= window // 2 else last_valid_atr
    atr_series = high_low.rolling(window=period, min_periods=1).mean()
    if atr_series.isna().all():
        return pd.Series(last_valid_atr, index=prices.index)
    return atr_series.fillna(method='bfill')

class DynamicGridBacktest:
    def __init__(self, capital=500e6, contract_value=100e3, margin_rate=0.2, fee_per_trade=0.47, 
                 grid_size_factor=1.47, minimum_grid_size=0.4, move_pivot=6, max_loss=20, take_profit_factor=1.0):
        self.capital = capital  
        self.contract_value = contract_value  
        self.margin_rate = margin_rate  
        self.fee_per_trade = fee_per_trade 
        self.max_contracts = 12  
        self.grid_size_factor = grid_size_factor  
        self.max_positions = 12  
        self.daily_fee_limit = 50000000  
        self.positions = []  
        self.equity = [capital]  
        self.trades = []  
        self.last_date = None  
        self.daily_fee = 0  
        self.log_file = "trade_log.txt" 
        self.equity_series = None 
        self.trade_history = []  
        self.short_atr_window = 60
        self.last_valid_atr = 1.0 
        self.minimum_grid_size = minimum_grid_size 
        self.move_pivot = move_pivot 
        self.max_loss = max_loss
        self.take_profit_factor = take_profit_factor
        self.daily_atr = None  
        self.current_pivot = None  

    def calculate_grid(self, current_price, current_atr, prices, index):
        if pd.isna(current_atr):
            print(f"Warning: current_atr is nan at index {index}, using last valid ATR: {self.last_valid_atr}")
            current_atr = self.last_valid_atr
        else:
            self.last_valid_atr = current_atr
        grid_size = max(self.grid_size_factor * current_atr, self.minimum_grid_size)
        grid_size = min(grid_size, 10)  
        size_per_level = 1
        total_open_contracts = sum(p[2] for p in self.positions)
        if total_open_contracts + size_per_level > self.max_contracts:
            size_per_level = 0
        if index >= self.short_atr_window:
            short_atr = calculate_atr(prices[:index + 1], window=self.short_atr_window, last_valid_atr=self.last_valid_atr)
            if pd.isna(short_atr):
                short_atr = self.last_valid_atr
            grid_size = max(grid_size, short_atr * self.grid_size_factor)
            grid_size = min(grid_size, 10)
        return grid_size, size_per_level

    def log_trade(self, timestamp, trade_type, price, size, profit=None, fee=None):
        with open(self.log_file, "a") as f:
            profit_str = f"{profit:,.0f}" if profit is not None else "N/A"
            fee_str = f"{fee:,.0f}" if fee is not None else "N/A"
            f.write(f"{timestamp},{trade_type},{price:.1f},{size:.4f},{profit_str},{fee_str}\n")

    def is_trading_time(self, timestamp):
        trading_start = time(9, 0)
        trading_end = time(14, 29)
        current_time = timestamp.time()
        return trading_start <= current_time <= trading_end

    def is_end_of_day(self, timestamp):
        end_start = time(14, 29)
        end_close = time(14, 30)
        current_time = timestamp.time()
        return end_start <= current_time <= end_close

    def check_daily_fee(self, timestamp, fee):
        current_date = timestamp.date()
        if self.last_date != current_date:
            self.daily_fee = 0
            self.last_date = current_date
        self.daily_fee += fee if fee is not None else 0
        return self.daily_fee > self.daily_fee_limit

    def apply_overnight_fee(self, timestamp):
        if self.positions:
            overnight_fee_per_position = 2550 
            total_overnight_fee = len(self.positions) * overnight_fee_per_position
            self.capital -= total_overnight_fee
            self.log_trade(timestamp, "OVERNIGHT_FEE", 0, len(self.positions), profit=0, fee=total_overnight_fee)
            self.trade_history.append((timestamp, "OVERNIGHT_FEE", 0, len(self.positions), 0, total_overnight_fee))

    def backtest(self, prices):
        """
        Run backtest on the given price series
        """
        if prices is None or prices.empty:
            print("No data available for backtest.")
            return None

        self.equity_series = pd.Series(index=prices.index)
        self.equity_series.iloc[0] = self.capital

        with open(self.log_file, "w") as f:
            f.write("Trade Log - Dynamic Grid Trading (6 Levels, Size=1, with Take Profit)\n")
            f.write("Time,Type,Price,Size,Profit,Fee\n")

        self.current_pivot = prices.iloc[0]  
        self.last_date = prices.index[0].date()
        last_data_date = prices.index[-1].date()  
        grid_size = None
        size = 0
        for i in range(1, len(prices)):
            if i % 10000 == 0:
                print(f"Processing {i / len(prices) * 100:.2f}%")

            current_price = prices.iloc[i]
        
            timestamp = prices.index[i]
            current_date = timestamp.date()

            if current_date != self.last_date:
                self.apply_overnight_fee(timestamp)  
                self.last_date = current_date

            if self.daily_atr is None and self.is_trading_time(timestamp):
                start_time = pd.Timestamp.combine(current_date, time(8, 45))
                end_time = pd.Timestamp.combine(current_date, time(9, 0))
                daily_initial_data = prices[(prices.index >= start_time) & (prices.index < end_time)]
                if len(daily_initial_data) >= 2:
                    self.daily_atr = calculate_atr(daily_initial_data, window=len(daily_initial_data))
                else:
                    self.daily_atr = self.last_valid_atr 

            if self.daily_atr is not None and self.is_trading_time(timestamp):
                current_atr = self.daily_atr
            else:
                current_atr = self.last_valid_atr 

            if current_date == last_data_date and timestamp.time() >= time(14, 29) and self.positions:
                self.close_all_positions(current_price, timestamp)
                self.equity_series.iloc[i] = self.capital
                continue

            if not self.is_trading_time(timestamp) and not self.is_end_of_day(timestamp) and self.positions:
                continue

            if not self.is_trading_time(timestamp) or self.is_end_of_day(timestamp):
                self.equity_series.iloc[i] = self.capital
                continue

            if grid_size is None:
                grid_size, size = self.calculate_grid(current_price, current_atr, prices, i)
            buypivot = self.current_pivot - self.move_pivot * grid_size
            sellpivot = self.current_pivot + self.move_pivot * grid_size

            if current_price < buypivot or current_price > sellpivot:
                self.close_all_positions(current_price, timestamp)
                self.current_pivot = current_price 
                current_atr=calculate_atr(prices[:i + 1], window=self.short_atr_window, last_valid_atr=self.last_valid_atr)
                grid_size, size = self.calculate_grid(current_price, current_atr, prices, i)

            self.max_loss_per_trade = 500000 * self.max_loss
            forced_close_triggered = False

            take_profit = self.take_profit_factor*grid_size * self.contract_value
            for pos in self.positions[:]:
                side, entry_price, size_pos = pos
                if side == "BUY":
                    profit = (current_price - entry_price) * size_pos * self.contract_value
                    if profit >= take_profit:
                        profit_before_fee = profit
                        fee = self.fee_per_trade * self.contract_value
                        profit = profit_before_fee - fee
                        self.capital += profit
                        self.positions.remove(pos)
                        self.log_trade(timestamp, "TAKE_PROFIT_BUY", current_price, size_pos, profit, fee)
                        self.trade_history.append((timestamp, "TAKE_PROFIT_BUY", current_price, size_pos, profit, fee))
                        continue
                else: 
                    profit = (entry_price - current_price) * size_pos * self.contract_value
                    if profit >= take_profit:
                        profit_before_fee = profit
                        fee = self.fee_per_trade * self.contract_value
                        profit = profit_before_fee - fee
                        self.capital += profit
                        self.positions.remove(pos)
                        self.log_trade(timestamp, "TAKE_PROFIT_SELL", current_price, size_pos, profit, fee)
                        self.trade_history.append((timestamp, "TAKE_PROFIT_SELL", current_price, size_pos, profit, fee))
                        continue

                if side == "BUY":
                    loss = (entry_price - current_price) * size_pos * self.contract_value if current_price < entry_price else 0
                    if loss >= self.max_loss_per_trade:
                        self.close_all_positions(current_price, timestamp)
                        self.current_pivot = current_price 
                        current_atr=calculate_atr(prices[:i + 1], window=self.short_atr_window, last_valid_atr=self.last_valid_atr)
                        grid_size, size = self.calculate_grid(current_price, current_atr, prices, i)
                        print(f"Max loss triggered for BUY at {timestamp}, closing all positions and moving pivot.")
                        forced_close_triggered = True
                        break
                else: 
                    loss = (current_price - entry_price) * size_pos * self.contract_value if current_price > entry_price else 0
                    if loss >= self.max_loss_per_trade:
                        self.close_all_positions(current_price, timestamp)
                        current_atr=calculate_atr(prices[:i + 1], window=self.short_atr_window, last_valid_atr=self.last_valid_atr)
                        self.current_pivot = current_price 
                        grid_size, size = self.calculate_grid(current_price, current_atr, prices, i)
                        print(f"Max loss triggered for SELL at {timestamp}, closing all positions and moving pivot.")
                        forced_close_triggered = True
                        break

            if len(self.positions) < self.max_positions and size > 0 and not self.check_daily_fee(timestamp, 0) and not forced_close_triggered:
                num_buys = len([p for p in self.positions if p[0] == "BUY"])
                num_sells = len([p for p in self.positions if p[0] == "SELL"])
                max_buys = 6  
                max_sells = 6  

                for n in range(1, 7):
                    buy_level = round(self.current_pivot - (n-0.5) * grid_size, 1)
                    sell_level = round(self.current_pivot + (n-0.5) * grid_size, 1)

                    if num_buys < max_buys and current_price <= buy_level and not any(p[0] == "BUY" and abs(p[1] - current_price) < grid_size * 0.5 for p in self.positions):
                        self.positions.append(("BUY", current_price, size))
                        self.trades.append(("BUY", current_price, current_price, size))
                        self.log_trade(timestamp, "BUY", current_price, size, fee=0)
                        self.trade_history.append((timestamp, "BUY", current_price, size, 0, 0))
                        num_buys += 1  

                        sell_positions = [p for p in self.positions if p[0] == "SELL"]
                        if sell_positions:
                            sell_pos = sell_positions[0]
                            profit_before_fee = (sell_pos[1] - current_price) * size * self.contract_value
                            fee = self.fee_per_trade * self.contract_value
                            profit = profit_before_fee - fee
                            self.capital += profit
                            self.positions.remove(sell_pos)
                            self.positions.remove(("BUY", current_price, size))
                            self.log_trade(timestamp, "CLOSE_PAIR", current_price, size, profit, fee)
                            self.trade_history.append((timestamp, "CLOSE_PAIR", current_price, size, profit, fee))
                            num_buys -= 1  
                            num_sells -= 1  
                            continue
                    
                    if num_sells < max_sells and current_price >= sell_level and not any(p[0] == "SELL" and abs(p[1] - current_price) < grid_size * 0.5 for p in self.positions):
                        self.positions.append(("SELL", current_price, size))
                        self.trades.append(("SELL", current_price, current_price, size))
                        self.log_trade(timestamp, "SELL", current_price, size, fee=0)
                        self.trade_history.append((timestamp, "SELL", current_price, size, 0, 0))
                        num_sells += 1  

                        buy_positions = [p for p in self.positions if p[0] == "BUY"]
                        if buy_positions:
                            buy_pos = buy_positions[0]
                            profit_before_fee = (current_price - buy_pos[1]) * size * self.contract_value
                            fee = self.fee_per_trade * self.contract_value
                            profit = profit_before_fee - fee
                            self.capital += profit
                            self.positions.remove(buy_pos)
                            self.positions.remove(("SELL", current_price, size))
                            self.log_trade(timestamp, "CLOSE_PAIR", current_price, size, profit, fee)
                            self.trade_history.append((timestamp, "CLOSE_PAIR", current_price, size, profit, fee))
                            num_buys -= 1 
                            num_sells -= 1  
                            continue

            current_equity = self.capital + sum((p[1] - current_price) * p[2] * self.contract_value if p[0] == "SELL" 
                                            else (current_price - p[1]) * p[2] * self.contract_value 
                                            for p in self.positions)
            self.equity_series.iloc[i] = current_equity

            if self.capital < 0:
                return None
            if i == len(prices) - 1 and self.positions:
                self.close_all_positions(current_price, timestamp)
                self.equity_series.iloc[i] = self.capital
        
        return self.calculate_performance_metrics()

    def close_all_positions(self, current_price, timestamp):
        total_profit = 0
        total_fee = 0
        for pos in self.positions[:]:
            side, entry_price, size = pos
            profit_before_fee = (current_price - entry_price if side == "BUY" else entry_price - current_price) * size * self.contract_value
            fee = self.fee_per_trade * self.contract_value
            profit = profit_before_fee - fee
            self.capital += profit
            self.positions.remove(pos)
            self.log_trade(timestamp, f"CLOSE_{side}", current_price, size, profit, fee)
            self.trade_history.append((timestamp, f"CLOSE_{side}", current_price, size, profit, fee))
            total_profit += profit
            total_fee += fee
        if total_fee > 0:
            self.log_trade(timestamp, "TOTAL_FEE", current_price, 0, profit=0, fee=total_fee)
            self.trade_history.append((timestamp, "TOTAL_FEE", current_price, 0, 0, total_fee))
        return total_profit

    def calculate_performance_metrics(self):
        """
        Calculate performance metrics based on backtest results
        """
        equity = self.equity_series.dropna()
        if equity.empty:
            print("Equity series is empty.")
            return None

        hpr = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
        days = (equity.index[-1] - equity.index[0]).days
        annual_return = ((1 + hpr / 100) ** (252 / days) - 1) * 100
        rolling_max = equity.cummax()
        drawdowns = (equity - rolling_max) / rolling_max * 100
        max_drawdown = drawdowns.min()

        in_drawdown = drawdowns < 0
        drawdown_changes = in_drawdown != in_drawdown.shift().fillna(False)
        drawdown_periods = drawdown_changes.cumsum()
        drawdown_durations = []
        for period in drawdown_periods[in_drawdown].unique():
            period_indices = equity.index[drawdown_periods == period]
            if len(period_indices) > 0:
                duration = (period_indices[-1] - period_indices[0]).days
                drawdown_durations.append(duration)
        longest_drawdown = max(drawdown_durations) if drawdown_durations else 0

        total_volume = sum(abs(trade[3]) for trade in self.trades)
        turnover_ratio = (total_volume * self.contract_value / self.capital) * 100

        daily_returns = self.equity_series.dropna().resample('D').last().pct_change().dropna()
        sharpe_std = daily_returns.std()
        sharpe_ratio = np.sqrt(252) * daily_returns.mean() / sharpe_std if sharpe_std != 0 else 0

        downside_returns = daily_returns[daily_returns < 0]
        downside_std = downside_returns.std() if not downside_returns.empty else 0
        sortino_ratio = np.sqrt(252) * daily_returns.mean() / downside_std if downside_std != 0 else 0

        return {
            'hpr': hpr,
            'annual_return': annual_return,
            'max_drawdown': max_drawdown,
            'longest_drawdown': longest_drawdown,
            'turnover_ratio': turnover_ratio,
            'sharpe_ratio': sharpe_ratio,
            'sortino_ratio': sortino_ratio,
            'daily_returns': daily_returns,
            'final_capital': equity.iloc[-1],
            'equity_series': equity
        }

    def print_results(self, output_dir=None):
        """
        Print and optionally save backtest results
        """
        metrics = self.calculate_performance_metrics()
        if metrics is None:
            print("Cannot calculate performance metrics due to missing data.")
            return

        total_trades = len(self.trades)
        total_profit = metrics['equity_series'].iloc[-1] - metrics['equity_series'].iloc[0]

        results = [
            f"Total trades: {total_trades}",
            f"Net profit: {total_profit:,.0f} VND",
            f"Holding Period Return (HPR): {metrics['hpr']:.2f}%",
            f"Annualized Return: {metrics['annual_return']:.2f}%",
            f"Maximum drawdown: {metrics['max_drawdown']:.2f}%",
            f"Longest Drawdown: {metrics['longest_drawdown']} days",
            f"Sharpe Ratio: {metrics['sharpe_ratio']:.2f}",
            f"Sortino Ratio: {metrics['sortino_ratio']:.2f}",
            f"Final capital: {metrics['final_capital']:,.0f} VND",
            f"Trade log saved to: {self.log_file}"
        ]
        
        # Print results to console
        for line in results:
            print(line)
            
        # Save results to file if output directory is provided
        if output_dir:
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)
                
            # Save performance metrics
            with open(os.path.join(output_dir, "performance_metrics.txt"), "w") as f:
                for line in results:
                    f.write(line + "\n")
            
            # Plot equity curve
            plt.plot(metrics['equity_series'])
            
            plt.title("Equity Curve")
            plt.xlabel("Time")
            plt.ylabel("Capital (VND)")
            plt.grid(True)
            plt.tight_layout()
            plt.savefig(os.path.join(output_dir, 'equity_curve.png'))
            
            # Save trade history to CSV
            # if self.trade_history:
            #     trade_df = pd.DataFrame(self.trade_history, 
            #                            columns=["Timestamp", "Type", "Price", "Size", "Profit", "Fee"])
            #     trade_df.to_csv(os.path.join(output_dir, "trade_history.csv"), index=False)
            
            # Save equity series to CSV
            metrics['equity_series'].to_csv(os.path.join(output_dir, "equity_series.csv"))
            
        return metrics 