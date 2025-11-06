"""
모든 전략 parameter_schema 테스트
"""
from programgarden_community.overseas_stock.strategy_conditions.sma_golden_dead import SMAGoldenDeadCross
from programgarden_community.overseas_stock.new_order_conditions.loss_cut import BasicLossCutManager
from programgarden_community.overseas_stock.new_order_conditions.stock_split_funds import StockSplitFunds
from programgarden_community.overseas_stock.cancel_order_conditions.price_range_canceller import PriceRangeCanceller
from programgarden_community.overseas_stock.modify_order_conditions.tracking_price import PricingRangeCanceller

strategies = [
    ("SMAGoldenDeadCross", SMAGoldenDeadCross),
    ("BasicLossCutManager", BasicLossCutManager),
    ("StockSplitFunds", StockSplitFunds),
    ("PriceRangeCanceller", PriceRangeCanceller),
    ("PricingRangeCanceller", PricingRangeCanceller),
]

for name, strategy_class in strategies:
    print("=" * 80)
    print(f"{name}")
    print("=" * 80)

    # ID 확인
    print(f"ID: {strategy_class.id}")
    print(f"Description: {strategy_class.description}")

    # Required 파라미터
    required = strategy_class.parameter_schema.get("required", [])
    print(f"Required: {required}")

    # 각 파라미터 요약
    properties = strategy_class.parameter_schema.get("properties", {})
    print(f"\nparameter_schema ({len(properties)} total):")
    for param_name, param_info in properties.items():
        title = param_info.get("title", param_name)
        desc = param_info.get("description", "N/A")
        is_required = param_name in required
        print(f"  - {param_name} ({title})")
        print(f"    Required: {is_required}, Desc: {desc[:50]}...")

    print("\n")
