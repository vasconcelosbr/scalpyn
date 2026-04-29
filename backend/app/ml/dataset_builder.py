"""Dataset Builder — Extract and prepare training data from trade_simulations."""

import logging
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import numpy as np
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.trade_simulation import TradeSimulation

logger = logging.getLogger(__name__)


class DatasetBuilder:
    """Build ML training datasets from trade simulation results."""

    # Core features from features_snapshot
    CORE_FEATURES = [
        "taker_ratio",
        "volume_delta",
        "rsi",
        "macd_histogram",
        "adx",
        "spread_pct",
        "volume_spike",
    ]

    # Trend features
    TREND_FEATURES = [
        "ema5",
        "ema9",
        "ema21",
        "ema50",
        "ema200",
        "ema9_gt_ema21",
        "ema50_gt_ema200",
    ]

    # Liquidity features
    LIQUIDITY_FEATURES = [
        "volume_24h_usdt",
        "orderbook_depth_usdt",
    ]

    # Microstructure features
    MICROSTRUCTURE_FEATURES = [
        "taker_buy_volume",
        "taker_sell_volume",
        "vwap_distance_pct",
    ]

    ALL_BASE_FEATURES = (
        CORE_FEATURES + TREND_FEATURES + LIQUIDITY_FEATURES + MICROSTRUCTURE_FEATURES
    )

    def __init__(self):
        """Initialize dataset builder."""
        pass

    async def load_simulations(
        self,
        db: AsyncSession,
        min_date: Optional[str] = None,
        max_date: Optional[str] = None,
        decision_type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Load trade simulations from database.

        Args:
            db: Database session
            min_date: Minimum timestamp_entry (ISO format)
            max_date: Maximum timestamp_entry (ISO format)
            decision_type: Filter by decision_type (ALLOW/BLOCK)
            limit: Maximum number of records to load

        Returns:
            List of simulation dictionaries
        """
        logger.info("Loading simulations from database...")

        query = select(TradeSimulation).where(
            TradeSimulation.is_simulated == True  # noqa: E712
        )

        if min_date:
            query = query.where(TradeSimulation.timestamp_entry >= min_date)
        if max_date:
            query = query.where(TradeSimulation.timestamp_entry <= max_date)
        if decision_type:
            query = query.where(TradeSimulation.decision_type == decision_type)

        # Order by timestamp to maintain time order
        query = query.order_by(TradeSimulation.timestamp_entry.asc())

        if limit:
            query = query.limit(limit)

        result = await db.execute(query)
        sims = result.scalars().all()

        logger.info(f"Loaded {len(sims)} simulations")

        return [
            {
                "id": str(sim.id),
                "symbol": sim.symbol,
                "timestamp_entry": sim.timestamp_entry,
                "entry_price": float(sim.entry_price),
                "tp_price": float(sim.tp_price),
                "sl_price": float(sim.sl_price),
                "exit_price": float(sim.exit_price) if sim.exit_price else None,
                "exit_timestamp": sim.exit_timestamp,
                "result": sim.result,
                "time_to_result": sim.time_to_result,
                "direction": sim.direction,
                "decision_type": sim.decision_type,
                "features_snapshot": sim.features_snapshot or {},
                "config_snapshot": sim.config_snapshot or {},
            }
            for sim in sims
        ]

    def extract_features(
        self, simulations: List[Dict[str, Any]]
    ) -> Tuple[pd.DataFrame, pd.Series]:
        """
        Extract features from simulations and create training dataset.

        Args:
            simulations: List of simulation dictionaries

        Returns:
            Tuple of (features_df, labels_series)
        """
        logger.info(f"Extracting features from {len(simulations)} simulations...")

        rows = []
        for sim in simulations:
            features_snap = sim.get("features_snapshot", {})

            row = {
                "sim_id": sim["id"],
                "symbol": sim["symbol"],
                "timestamp_entry": sim["timestamp_entry"],
                "direction": sim["direction"],
            }

            # Extract base features
            for feat in self.ALL_BASE_FEATURES:
                value = features_snap.get(feat)

                # Handle boolean features
                if isinstance(value, bool):
                    row[feat] = 1.0 if value else 0.0
                elif value is not None:
                    row[feat] = float(value)
                else:
                    row[feat] = None

            # Add to rows
            rows.append(row)

        df = pd.DataFrame(rows)

        logger.info(f"Created dataframe with shape {df.shape}")
        logger.info(f"Columns: {df.columns.tolist()}")

        # Create labels: WIN=1, LOSS/TIMEOUT=0
        labels = pd.Series([1 if sim["result"] == "WIN" else 0 for sim in simulations])

        return df, labels

    def engineer_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Create engineered features from base features.

        Args:
            df: DataFrame with base features

        Returns:
            DataFrame with additional engineered features
        """
        logger.info("Engineering features...")

        df = df.copy()

        # Flow strength = taker_ratio * volume_delta
        if "taker_ratio" in df.columns and "volume_delta" in df.columns:
            df["flow_strength"] = df["taker_ratio"] * df["volume_delta"]
        else:
            df["flow_strength"] = 0.0

        # Trend alignment = ema9_gt_ema21 + ema50_gt_ema200
        if "ema9_gt_ema21" in df.columns and "ema50_gt_ema200" in df.columns:
            df["trend_alignment"] = df["ema9_gt_ema21"] + df["ema50_gt_ema200"]
        else:
            df["trend_alignment"] = 0.0

        # Momentum strength = macd_histogram * adx
        if "macd_histogram" in df.columns and "adx" in df.columns:
            df["momentum_strength"] = df["macd_histogram"] * df["adx"]
        else:
            df["momentum_strength"] = 0.0

        # Delta normalized = volume_delta / volume_24h_usdt
        if "volume_delta" in df.columns and "volume_24h_usdt" in df.columns:
            df["delta_normalized"] = df["volume_delta"] / df["volume_24h_usdt"].replace(0, np.nan)
            df["delta_normalized"] = df["delta_normalized"].fillna(0.0)
        else:
            df["delta_normalized"] = 0.0

        # EMA distance percentage (derived)
        if "ema9" in df.columns and "ema21" in df.columns and "ema21" in df.columns:
            df["ema_distance_pct"] = (
                (df["ema9"] - df["ema21"]) / df["ema21"].replace(0, np.nan) * 100
            )
            df["ema_distance_pct"] = df["ema_distance_pct"].fillna(0.0)
        else:
            df["ema_distance_pct"] = 0.0

        logger.info(f"Engineered features. New shape: {df.shape}")

        return df

    def encode_direction(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Encode direction as numeric feature.

        Args:
            df: DataFrame with 'direction' column

        Returns:
            DataFrame with direction_encoded column
        """
        direction_map = {
            "LONG": 1,
            "SHORT": -1,
            "SPOT": 0,
        }

        df = df.copy()
        df["direction_encoded"] = df["direction"].map(direction_map).fillna(0)

        return df

    def get_feature_columns(self, df: pd.DataFrame) -> List[str]:
        """
        Get list of feature columns for training (excluding metadata).

        Args:
            df: DataFrame with all columns

        Returns:
            List of feature column names
        """
        # Metadata columns to exclude
        metadata_cols = ["sim_id", "symbol", "timestamp_entry", "direction"]

        # Get all columns except metadata
        feature_cols = [col for col in df.columns if col not in metadata_cols]

        return feature_cols

    def prepare_dataset(
        self,
        simulations: List[Dict[str, Any]],
        add_direction_feature: bool = True,
    ) -> Tuple[pd.DataFrame, pd.Series, List[str]]:
        """
        Complete data preparation pipeline.

        Args:
            simulations: List of simulation dictionaries
            add_direction_feature: Whether to add direction_encoded feature

        Returns:
            Tuple of (features_df, labels_series, feature_columns)
        """
        if not simulations:
            raise ValueError("No simulations provided")

        # Extract base features
        df, labels = self.extract_features(simulations)

        # Engineer features
        df = self.engineer_features(df)

        # Encode direction
        if add_direction_feature:
            df = self.encode_direction(df)

        # Get feature columns
        feature_cols = self.get_feature_columns(df)

        # Fill missing values with 0
        df[feature_cols] = df[feature_cols].fillna(0.0)

        logger.info(f"Dataset prepared: {len(df)} samples, {len(feature_cols)} features")
        logger.info(f"Label distribution: {labels.value_counts().to_dict()}")

        return df, labels, feature_cols

    def time_based_split(
        self,
        df: pd.DataFrame,
        labels: pd.Series,
        train_ratio: float = 0.8,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
        """
        Split data using time-based approach (no shuffle).

        Args:
            df: Features DataFrame (must have timestamp_entry)
            labels: Labels Series
            train_ratio: Ratio of data for training

        Returns:
            Tuple of (X_train, X_val, y_train, y_val)
        """
        # Ensure data is sorted by time
        df_sorted = df.sort_values("timestamp_entry").reset_index(drop=True)
        labels_sorted = labels[df_sorted.index].reset_index(drop=True)

        # Split at the time boundary
        split_idx = int(len(df_sorted) * train_ratio)

        X_train = df_sorted.iloc[:split_idx]
        X_val = df_sorted.iloc[split_idx:]
        y_train = labels_sorted.iloc[:split_idx]
        y_val = labels_sorted.iloc[split_idx:]

        logger.info(
            f"Time-based split: train={len(X_train)} ({len(y_train[y_train==1])} wins), "
            f"val={len(X_val)} ({len(y_val[y_val==1])} wins)"
        )

        return X_train, X_val, y_train, y_val
