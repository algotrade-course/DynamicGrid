import argparse
import yaml
import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
import optuna
import shutil

from data_fetcher import prepare_data
from logic import DynamicGridBacktest


def setup_results_dir(config, run_mode):
    """
    Setup the results directory structure
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_dir = config['results']['base_directory']
    output_dir = os.path.join(base_dir, run_mode, timestamp)
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    # Save a copy of the config file
    # with open(os.path.join(output_dir, 'config.yaml'), 'w') as f:
    #     yaml.dump(config, f, default_flow_style=False)
        
    return output_dir


def run_backtest(config, data_mode, output_dir):
    """
    Run a backtest using the specified configuration and data mode
    """
    # Prepare data
    prices = prepare_data(config, data_mode)
    
    if prices is None:
        print(f"Error: Failed to load {data_mode} data.")
        return None
    
    print(f"Running backtest on {data_mode} data with {len(prices)} price points...")
    
    # Create backtest instance with parameters from config
    backtest = DynamicGridBacktest(
        capital=config['strategy']['capital'],
        contract_value=config['strategy']['contract_value'],
        margin_rate=config['strategy']['margin_rate'],
        fee_per_trade=config['strategy']['fee_per_trade'],
        grid_size_factor=config['strategy']['grid_size_factor'],
        minimum_grid_size=config['strategy']['minimum_grid_size'],
        move_pivot=config['strategy']['move_pivot'],
        # max_loss=config['strategy']['max_loss'],
        take_profit_factor=config['strategy']['take_profit_factor']
    )
    
    # Run backtest
    backtest.log_file = os.path.join(output_dir, f"trade_log.txt")
    metrics = backtest.backtest(prices)
    
    # Print and save results
    print(f"\nBacktest results ({data_mode}):")
    backtest.print_results(output_dir)
    
    return metrics


def objective(trial, config, prices):
    """
    Objective function for Optuna optimization
    """
    # Get parameter ranges from config or use defaults
    opt_config = config.get('optimization', {})
    
    grid_size_factor_range = opt_config.get('grid_size_factor_range', [1.0, 10.0])
    minimum_grid_size_range = opt_config.get('minimum_grid_size_range', [0.6, 2.0])
    move_pivot_range = opt_config.get('move_pivot_range', [6, 12])
    take_profit_factor_range = opt_config.get('take_profit_factor_range', [1, 2])
    
    # Sample parameters
    grid_size_factor = trial.suggest_float("grid_size_factor", 
                                          grid_size_factor_range[0], 
                                          grid_size_factor_range[1], 
                                          step=0.25)
    
    minimum_grid_size = trial.suggest_float("minimum_grid_size", 
                                           minimum_grid_size_range[0], 
                                           minimum_grid_size_range[1], 
                                           step=0.1)
    
    move_pivot = trial.suggest_int("move_pivot", 
                                  move_pivot_range[0], 
                                  move_pivot_range[1])
    
    take_profit_factor = trial.suggest_float("take_profit_factor", 
                                            take_profit_factor_range[0], 
                                            take_profit_factor_range[1], 
                                            step=0.5)
    
    # Create backtest instance with trial parameters
    backtest = DynamicGridBacktest(
        capital=config['strategy']['capital'],
        contract_value=config['strategy']['contract_value'],
        margin_rate=config['strategy']['margin_rate'],
        fee_per_trade=config['strategy']['fee_per_trade'],
        grid_size_factor=grid_size_factor,
        minimum_grid_size=minimum_grid_size,
        move_pivot=move_pivot,
        # max_loss=config['strategy']['max_loss'],
        take_profit_factor=take_profit_factor
    )
    
    # Run backtest
    metrics = backtest.backtest(prices)
    
    if metrics is None or 'sharpe_ratio' not in metrics or 'final_capital' not in metrics:
        return -float('inf')
    
    final_capital = metrics['final_capital']
    sharpe_ratio = metrics['sharpe_ratio']

    if final_capital < 0:
        return -float('inf')
    
    trial.set_user_attr('final_capital', final_capital)
    trial.set_user_attr('metrics', metrics)
    
    return sharpe_ratio


def log_trial_callback(study, trial, output_dir):
    """
    Callback function for logging trial results
    """
    final_capital = trial.user_attrs.get('final_capital', 0)
    log_file = os.path.join(output_dir, "optuna_trials.txt")
    
    with open(log_file, "a") as f:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        trial_info = (
            f"[{timestamp}] Trial {trial.number} finished with Sharpe Ratio: {trial.value:.2f}, "
            f"Final Capital: {final_capital:,.0f} VND "
            f"and parameters: {trial.params}. Best is trial {study.best_trial.number} "
            f"with Sharpe Ratio: {study.best_value:.2f}."
        )
        f.write(trial_info + "\n")
    print(trial_info)


def run_optimization(config, output_dir):
    """
    Run parameter optimization using Optuna
    """
    # Prepare in-sample data for optimization
    prices = prepare_data(config, "in_sample")
    
    if prices is None:
        print("Error: Failed to load in-sample data for optimization.")
        return
    
    print(f"Running optimization on in-sample data with {len(prices)} price points...")
    
    # Create callback with output_dir
    callback = lambda study, trial: log_trial_callback(study, trial, output_dir)
    
    # Create and configure the study
    study = optuna.create_study(direction="maximize")
    n_trials = config.get('optimization', {}).get('n_trials', 100)
    
    # Run optimization
    study.optimize(
        lambda trial: objective(trial, config, prices), 
        n_trials=n_trials, 
        callbacks=[callback]
    )
    
    # Get best parameters
    best_trial = study.best_trial
    best_params = best_trial.params
    best_metrics = best_trial.user_attrs.get('metrics', {})
    
    print("\nOptimization Results:")
    print(f"Best Sharpe Ratio: {best_trial.value:.2f}")
    print(f"Best Parameters: {best_params}")
    print(f"Final Capital: {best_trial.user_attrs.get('final_capital', 0):,.0f} VND")
    
    # Save best parameters
    with open(os.path.join(output_dir, "best_parameters.yaml"), "w") as f:
        yaml.dump(best_params, f, default_flow_style=False)
    
    # Create a new config with the best parameters
    optimized_config = config.copy()
    for param, value in best_params.items():
        optimized_config['strategy'][param] = value
    
    # Save the optimized config
    # with open(os.path.join(output_dir, "optimized_config.yaml"), "w") as f:
    #     yaml.dump(optimized_config, f, default_flow_style=False)
    
    # Run a backtest with the best parameters
    print("\nRunning backtest with optimized parameters...")
    optimized_dir = os.path.join(output_dir, "optimized_backtest")
    os.makedirs(optimized_dir, exist_ok=True)
    
    backtest = DynamicGridBacktest(
        capital=config['strategy']['capital'],
        contract_value=config['strategy']['contract_value'],
        margin_rate=config['strategy']['margin_rate'],
        fee_per_trade=config['strategy']['fee_per_trade'],
        grid_size_factor=best_params['grid_size_factor'],
        minimum_grid_size=best_params['minimum_grid_size'],
        move_pivot=best_params['move_pivot'],
        # max_loss=config['strategy']['max_loss'],
        take_profit_factor=best_params['take_profit_factor']
    )
    
    backtest.log_file = os.path.join(optimized_dir, "trade_log.txt")
    backtest.backtest(prices)
    backtest.print_results(optimized_dir)
    
    return best_params


def main():
    """
    Main function to run the driver
    """
    parser = argparse.ArgumentParser(description='Grid Trading Backtest and Optimization')
    parser.add_argument('--mode', type=str, choices=['backtest', 'optimize'], required=True,
                       help='Run mode: backtest or optimize')
    parser.add_argument('--data', type=str, choices=['in_sample', 'out_sample'], default='in_sample',
                       help='Data to use for backtest (fetched data will be used in place if fetch_data is true)')
    parser.add_argument('--config', type=str, default='config/config.yaml',
                       help='Path to config file')
    
    args = parser.parse_args()
    
    # Load configuration
    try:
        with open(args.config, 'r') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"Error loading config file: {e}")
        return
    
    # Setup results directory
    output_dir = setup_results_dir(config, args.mode)
    print(f"Results will be saved to: {output_dir}")
    
    if args.mode == 'backtest':
        run_backtest(config, args.data, output_dir)
    elif args.mode == 'optimize':
        if args.data == 'out_sample':
            print("Warning: Optimization should only be run on in-sample data. Switching to in-sample.")
        run_optimization(config, output_dir)
        if os.path.exists("trade_log.txt"):
            os.remove("trade_log.txt")
            print("Removed redundant trade_log.txt from root directory")
    
    print(f"\nAll results saved to: {output_dir}")


if __name__ == "__main__":
    main() 