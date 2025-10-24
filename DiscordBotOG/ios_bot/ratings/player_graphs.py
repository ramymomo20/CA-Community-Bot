"""
Player Graph Generation System
Creates matplotlib graphs for player weekly breakdowns and statistics.
"""

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np
import os
import io
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

# Try to import seaborn, but don't fail if it's not available
try:
    import seaborn as sns
    # Set style for better-looking graphs
    try:
        plt.style.use('seaborn-v0_8')
        sns.set_palette("husl")
    except:
        # Fallback to default style if seaborn style not available
        plt.style.use('default')
except ImportError:
    # Use default matplotlib style if seaborn not available
    plt.style.use('default')

def create_weekly_breakdown_graph(player_stats_rows: List[Dict], steam_id: str, player_name: str) -> Optional[bytes]:
    """
    Create a weekly breakdown graph for a player's performance.
    
    Args:
        player_stats_rows: List of player match statistics
        steam_id: Player's Steam ID
        player_name: Player's name
        
    Returns:
        Bytes of the generated graph image, or None if no data
    """
    if not player_stats_rows:
        return None
    
    try:
        # Convert to DataFrame
        df = pd.DataFrame(player_stats_rows)
        
        # Parse datetime
        df['datetime'] = pd.to_datetime(df['datetime'])
        
        # Add week column
        df['week'] = df['datetime'].dt.isocalendar().week
        df['year'] = df['datetime'].dt.isocalendar().year
        df['year_week'] = df['year'].astype(str) + '-W' + df['week'].astype(str).str.zfill(2)
        
        # Convert numeric columns to proper data types
        numeric_columns = ['goals', 'assists', 'passesCompleted', 'passes', 'shots', 'shotsOnGoal', 'interceptions', 'slidingTacklesCompleted', 'fouls']
        for col in numeric_columns:
            if col in df.columns:
                # Convert to numeric, coercing errors to NaN, then fill NaN with 0
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            else:
                df[col] = 0
        
        # Group by week and calculate weekly stats
        weekly_stats = df.groupby('year_week').agg({
            'goals': 'sum',
            'assists': 'sum', 
            'passesCompleted': 'sum',
            'passes': 'sum',
            'shots': 'sum',
            'shotsOnGoal': 'sum',
            'interceptions': 'sum',
            'slidingTacklesCompleted': 'sum',
            'fouls': 'sum'
        })
        
        # Add matches count
        match_counts = df.groupby('year_week').size().reset_index(name='matches')
        weekly_stats = weekly_stats.merge(match_counts, on='year_week')
        
        if weekly_stats.empty:
            return None
        
        # Create the figure with subplots
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(15, 12))
        fig.suptitle(f'Weekly Performance Breakdown: {player_name}', fontsize=16, fontweight='bold')
        
        # 1. Goals and Assists (Line Chart)
        ax1.plot(weekly_stats.index, weekly_stats['goals'], marker='o', linewidth=2, label='Goals', color='#FF6B6B')
        ax1.plot(weekly_stats.index, weekly_stats['assists'], marker='s', linewidth=2, label='Assists', color='#4ECDC4')
        ax1.set_title('Goals & Assists per Week', fontweight='bold')
        ax1.set_ylabel('Count')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # Rotate x-axis labels for better readability
        ax1.tick_params(axis='x', rotation=45)
        
        # 2. Shooting Performance (Bar Chart)
        x = np.arange(len(weekly_stats.index))
        width = 0.35
        
        ax2.bar(x - width/2, weekly_stats['shots'], width, label='Total Shots', color='#95E1D3', alpha=0.8)
        ax2.bar(x + width/2, weekly_stats['shotsOnGoal'], width, label='Shots on Goal', color='#F38181', alpha=0.8)
        ax2.set_title('Shooting Performance per Week', fontweight='bold')
        ax2.set_ylabel('Count')
        ax2.set_xticks(x)
        ax2.set_xticklabels(weekly_stats.index, rotation=45)
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        
        # 3. Passing Performance (Line Chart with Area)
        # Avoid division by zero
        pass_accuracy = np.where(weekly_stats['passes'] > 0, 
                                (weekly_stats['passesCompleted'] / weekly_stats['passes'] * 100), 0)
        ax3.plot(weekly_stats.index, pass_accuracy, marker='o', linewidth=2, color='#A8E6CF')
        ax3.fill_between(weekly_stats.index, pass_accuracy, alpha=0.3, color='#A8E6CF')
        ax3.set_title('Pass Completion Rate per Week', fontweight='bold')
        ax3.set_ylabel('Completion Rate (%)')
        ax3.set_ylim(0, 100)
        ax3.grid(True, alpha=0.3)
        ax3.tick_params(axis='x', rotation=45)
        
        # 4. Defensive Actions (Stacked Bar Chart)
        ax4.bar(weekly_stats.index, weekly_stats['interceptions'], label='Interceptions', color='#FFD93D', alpha=0.8)
        ax4.bar(weekly_stats.index, weekly_stats['slidingTacklesCompleted'], 
                bottom=weekly_stats['interceptions'], label='Tackles', color='#6C5CE7', alpha=0.8)
        ax4.set_title('Defensive Actions per Week', fontweight='bold')
        ax4.set_ylabel('Count')
        ax4.set_xticklabels(weekly_stats.index, rotation=45)
        ax4.legend()
        ax4.grid(True, alpha=0.3)
        
        # Adjust layout
        plt.tight_layout()
        
        # Save to bytes
        img_buffer = io.BytesIO()
        plt.savefig(img_buffer, format='png', dpi=300, bbox_inches='tight')
        img_buffer.seek(0)
        
        # Close the plot to free memory
        plt.close()
        
        return img_buffer.getvalue()
        
    except Exception as e:
        print(f"Error creating weekly breakdown graph: {e}")
        return None

def create_performance_trend_graph(player_stats_rows: List[Dict], steam_id: str, player_name: str) -> Optional[bytes]:
    """
    Create a performance trend graph showing player improvement over time.
    
    Args:
        player_stats_rows: List of player match statistics
        steam_id: Player's Steam ID
        player_name: Player's name
        
    Returns:
        Bytes of the generated graph image, or None if no data
    """
    if not player_stats_rows:
        return None
    
    try:
        # Convert to DataFrame
        df = pd.DataFrame(player_stats_rows)
        
        # Parse datetime
        df['datetime'] = pd.to_datetime(df['datetime'])
        df = df.sort_values('datetime')
        
        # Convert numeric columns to proper data types
        numeric_columns = ['goals', 'assists', 'passesCompleted', 'passes', 'shots', 'shotsOnGoal', 'interceptions', 'slidingTacklesCompleted', 'fouls']
        for col in numeric_columns:
            if col in df.columns:
                # Convert to numeric, coercing errors to NaN, then fill NaN with 0
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            else:
                df[col] = 0
        
        # Calculate rolling averages for key metrics
        window_size = min(5, len(df))  # Use 5 matches or less if fewer matches
        
        df['rolling_goals'] = df['goals'].rolling(window=window_size, min_periods=1).mean()
        df['rolling_assists'] = df['assists'].rolling(window=window_size, min_periods=1).mean()
        
        # Calculate pass completion rate safely
        pass_completion = np.where(df['passes'] > 0, (df['passesCompleted'] / df['passes'] * 100), 0)
        df['rolling_passes'] = pd.Series(pass_completion).rolling(window=window_size, min_periods=1).mean()
        
        # Create the figure
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 10))
        fig.suptitle(f'Performance Trends: {player_name}', fontsize=16, fontweight='bold')
        
        # 1. Goals and Assists Trend
        ax1.plot(df['datetime'], df['rolling_goals'], marker='o', linewidth=2, label='Avg Goals (5-match)', color='#FF6B6B')
        ax1.plot(df['datetime'], df['rolling_assists'], marker='s', linewidth=2, label='Avg Assists (5-match)', color='#4ECDC4')
        ax1.scatter(df['datetime'], df['goals'], alpha=0.5, color='#FF6B6B', s=30, label='Individual Goals')
        ax1.scatter(df['datetime'], df['assists'], alpha=0.5, color='#4ECDC4', s=30, label='Individual Assists')
        ax1.set_title('Goals & Assists Trend', fontweight='bold')
        ax1.set_ylabel('Count')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # Format x-axis dates
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
        ax1.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
        ax1.tick_params(axis='x', rotation=45)
        
        # 2. Pass Completion Trend
        ax2.plot(df['datetime'], df['rolling_passes'], marker='o', linewidth=2, color='#A8E6CF')
        ax2.scatter(df['datetime'], pass_completion, alpha=0.5, color='#A8E6CF', s=30)
        ax2.set_title('Pass Completion Rate Trend', fontweight='bold')
        ax2.set_ylabel('Completion Rate (%)')
        ax2.set_ylim(0, 100)
        ax2.grid(True, alpha=0.3)
        
        # Format x-axis dates
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
        ax2.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
        ax2.tick_params(axis='x', rotation=45)
        
        # Add trend line
        if len(df) > 1:
            z = np.polyfit(range(len(df)), df['rolling_passes'], 1)
            p = np.poly1d(z)
            ax2.plot(df['datetime'], p(range(len(df))), "r--", alpha=0.8, label='Trend Line')
            ax2.legend()
        
        # Adjust layout
        plt.tight_layout()
        
        # Save to bytes
        img_buffer = io.BytesIO()
        plt.savefig(img_buffer, format='png', dpi=300, bbox_inches='tight')
        img_buffer.seek(0)
        
        # Close the plot to free memory
        plt.close()
        
        return img_buffer.getvalue()
        
    except Exception as e:
        print(f"Error creating performance trend graph: {e}")
        return None

def create_radar_chart(player_stats_rows: List[Dict], steam_id: str, player_name: str) -> Optional[bytes]:
    """
    Create a radar chart showing player's overall performance profile.
    
    Args:
        player_stats_rows: List of player match statistics
        steam_id: Player's Steam ID
        player_name: Player's name
        
    Returns:
        Bytes of the generated graph image, or None if no data
    """
    if not player_stats_rows:
        return None
    
    try:
        # Convert to DataFrame
        df = pd.DataFrame(player_stats_rows)
        
        # Convert numeric columns to proper data types
        numeric_columns = ['goals', 'assists', 'passesCompleted', 'passes', 'shots', 'shotsOnGoal', 'interceptions', 'slidingTacklesCompleted', 'fouls']
        for col in numeric_columns:
            if col in df.columns:
                # Convert to numeric, coercing errors to NaN, then fill NaN with 0
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            else:
                df[col] = 0
        
        # Calculate average stats
        avg_stats = df.agg({
            'goals': 'mean',
            'assists': 'mean',
            'passesCompleted': 'mean',
            'passes': 'mean',
            'shots': 'mean',
            'shotsOnGoal': 'mean',
            'interceptions': 'mean',
            'slidingTacklesCompleted': 'mean'
        })
        
        # Calculate derived metrics safely
        pass_accuracy = (avg_stats['passesCompleted'] / avg_stats['passes'] * 100) if avg_stats['passes'] > 0 else 0
        shot_accuracy = (avg_stats['shotsOnGoal'] / avg_stats['shots'] * 100) if avg_stats['shots'] > 0 else 0
        
        # Define categories and values
        categories = ['Goals', 'Assists', 'Pass Accuracy', 'Shot Accuracy', 'Interceptions', 'Tackles']
        values = [
            float(avg_stats['goals']) * 10,  # Scale up for better visualization
            float(avg_stats['assists']) * 10,
            float(pass_accuracy),
            float(shot_accuracy),
            float(avg_stats['interceptions']),
            float(avg_stats['slidingTacklesCompleted'])
        ]
        
        # Number of variables
        N = len(categories)
        
        # Create the figure
        fig, ax = plt.subplots(figsize=(10, 10), subplot_kw=dict(projection='polar'))
        
        # Compute angle for each axis
        angles = [n / float(N) * 2 * np.pi for n in range(N)]
        angles += angles[:1]  # Complete the circle
        
        # Add the first value again to close the plot
        values += values[:1]
        
        # Plot the data
        ax.plot(angles, values, 'o-', linewidth=2, color='#FF6B6B')
        ax.fill(angles, values, alpha=0.25, color='#FF6B6B')
        
        # Set the labels
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(categories)
        
        # Set the title
        ax.set_title(f'Performance Profile: {player_name}', fontsize=16, fontweight='bold', pad=20)
        
        # Set the y-axis limits
        ax.set_ylim(0, max(values) * 1.1)
        
        # Add grid
        ax.grid(True)
        
        # Adjust layout
        plt.tight_layout()
        
        # Save to bytes
        img_buffer = io.BytesIO()
        plt.savefig(img_buffer, format='png', dpi=300, bbox_inches='tight')
        img_buffer.seek(0)
        
        # Close the plot to free memory
        plt.close()
        
        return img_buffer.getvalue()
        
    except Exception as e:
        print(f"Error creating radar chart: {e}")
        return None 