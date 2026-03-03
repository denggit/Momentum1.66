#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author     : Zijun Deng
@Date       : 3/1/26 10:10 PM
@File       : train.py
@Description:
"""
import os
import sys

# 添加项目根目录到 Python 路径
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(current_file))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import pandas as pd
import xgboost as xgb
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import train_test_split
from src.utils.log import get_logger
logger = get_logger(__name__)

ML_DATASET_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "data", "reports",
                               "SMC", 'SMC_ML_Dataset_COMBINE.csv')
# 1. 加载数据
df = pd.read_csv(ML_DATASET_FILE)
df['Entry_Time'] = pd.to_datetime(df['Entry_Time'])

# 🔪 【核心切分】：只用 2023 年 12 月 31 日之前的数据训练！
df_train = df[(df['Entry_Time'] >= '2020-01-01') & (df['Entry_Time'] < '2023-12-31')].copy()
# 剩下的留给引擎去盲测
logger.info(f"训练集样本数: {len(df_train)}")

# 2. 区分 X (考题) 和 Y (答案)
# 严禁泄露未来函数：把时间、PnL、Type等结果列全部丢弃，只留下当时那一瞬间的特征！
features = ['Hour', 'DayOfWeek', 'Dist_to_EMA', 'ADX', 'RSI', 'ATR_Rank', 'ATR_Slope', 'Body_Ratio', 'sl_pct']
X = df_train[features]
y = df_train['Label']

# 3. 划分训练集 (80%) 和 测试集 (20%)
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

# 4. 召唤 XGBoost 风控官
logger.info("🧠 正在训练 XGBoost AI 模型...")
model = xgb.XGBClassifier(
    n_estimators=100,
    learning_rate=0.05,
    max_depth=3,  # 限制深度，防止死记硬背(过拟合)
    subsample=0.8,
    random_state=42,
    eval_metric='logloss'
)

model.fit(X_train, y_train)

# 5. 考试出成绩！
y_pred = model.predict(X_test)
accuracy = accuracy_score(y_test, y_pred)
logger.info("\n" + "=" * 50)
logger.info(f"🎯 AI 模型盲测准确率 (Accuracy): {accuracy * 100:.2f}%")
logger.info("=" * 50)
logger.info("📊 详细诊断报告:")
logger.info(classification_report(y_test, y_pred))

# 6. 揭秘：到底是哪个特征最影响 SMC 的胜率？
importance = model.feature_importances_
feature_imp = pd.DataFrame({'Feature': features, 'Importance': importance})
feature_imp = feature_imp.sort_values(by='Importance', ascending=False)

logger.info("\n👑 核心破案线索 (特征重要性排行榜):")
logger.info(feature_imp)

# 保存模型
model_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "data", "models")
os.makedirs(model_dir, exist_ok=True)
model_name = "smc_combine_v1.json"
model.save_model(os.path.join(model_dir, model_name))
logger.info(f"💾 模型已保存至: src/ai/models/{model_name}")
