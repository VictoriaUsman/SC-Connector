"""Tests for shared.finance_buckets — classifying Finances API breakdown
lines into the same category buckets Amazon's UI Transaction Report uses."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("ENVIRONMENT", "staging")


class TestBucketFor:
    def test_principal_is_product_sales(self):
        from shared.finance_buckets import PRODUCT_SALES, bucket_for

        assert bucket_for("Principal") == PRODUCT_SALES
        assert bucket_for("ProductCharges / Principal") == PRODUCT_SALES

    def test_product_tax_is_product_sales_tax(self):
        from shared.finance_buckets import PRODUCT_SALES_TAX, bucket_for

        assert bucket_for("ProductCharges / Tax") == PRODUCT_SALES_TAX
        assert bucket_for("Tax") == PRODUCT_SALES_TAX

    def test_shipping_credit_vs_shipping_tax(self):
        from shared.finance_buckets import SHIPPING_CREDITS, SHIPPING_CREDITS_TAX, bucket_for

        assert bucket_for("ShippingCharge") == SHIPPING_CREDITS
        assert bucket_for("ShippingChargeTax") == SHIPPING_CREDITS_TAX

    def test_shipping_chargeback_and_holdback_are_other_transaction_fees(self):
        from shared.finance_buckets import OTHER_TRANSACTION_FEES, bucket_for

        assert bucket_for("ShippingChargeback") == OTHER_TRANSACTION_FEES
        assert bucket_for("ShippingHoldback") == OTHER_TRANSACTION_FEES

    def test_giftwrap_credit_vs_tax(self):
        from shared.finance_buckets import GIFT_WRAP_CREDITS, GIFT_WRAP_CREDITS_TAX, bucket_for

        assert bucket_for("GiftWrapCharge") == GIFT_WRAP_CREDITS
        assert bucket_for("GiftWrapChargeTax") == GIFT_WRAP_CREDITS_TAX

    def test_regulatory_fee_vs_tax_on_regulatory_fee(self):
        from shared.finance_buckets import REGULATORY_FEE, TAX_ON_REGULATORY_FEE, bucket_for

        assert bucket_for("RegulatoryFee") == REGULATORY_FEE
        assert bucket_for("RegulatoryFeeTax") == TAX_ON_REGULATORY_FEE

    def test_promotion_vs_promotion_tax(self):
        from shared.finance_buckets import PROMOTIONAL_REBATES, PROMOTIONAL_REBATES_TAX, bucket_for

        assert bucket_for("PromotionAmount") == PROMOTIONAL_REBATES
        assert bucket_for("PromotionTax") == PROMOTIONAL_REBATES_TAX

    def test_marketplace_facilitator_tax_is_marketplace_withheld_tax(self):
        from shared.finance_buckets import MARKETPLACE_WITHHELD_TAX, bucket_for

        assert bucket_for("MarketplaceFacilitatorTax") == MARKETPLACE_WITHHELD_TAX

    def test_commission_and_referral_fee_are_selling_fees(self):
        from shared.finance_buckets import SELLING_FEES, bucket_for

        assert bucket_for("Commission") == SELLING_FEES
        assert bucket_for("ReferralFee") == SELLING_FEES
        assert bucket_for("VariableClosingFee") == SELLING_FEES

    def test_fba_per_unit_fee_is_fba_fees_only_within_an_order(self):
        from shared.finance_buckets import FBA_FEES, OTHER_TRANSACTION_FEES, bucket_for

        assert bucket_for("FBAPerUnitFulfillmentFee", transaction_type="Shipment") == FBA_FEES
        assert bucket_for("FBAPerUnitFulfillmentFee", transaction_type="Order") == FBA_FEES
        # No transaction_type given -> can't confirm it rode with an order,
        # so it falls to the wider "other transaction fees" bucket.
        assert bucket_for("FBAPerUnitFulfillmentFee") == OTHER_TRANSACTION_FEES

    def test_standalone_fba_event_is_other_transaction_fees_regardless_of_type(self):
        # Mirrors Amazon's UI report: "FBA Customer Return Fee" rows land in
        # "other transaction fees", not "fba fees", even though it's FBA.
        from shared.finance_buckets import OTHER_TRANSACTION_FEES, bucket_for

        assert bucket_for("FBACustomerReturnPerUnitFee", transaction_type="Shipment") == OTHER_TRANSACTION_FEES

    def test_per_item_fee_and_tax_collection_fee_are_other_transaction_fees(self):
        from shared.finance_buckets import OTHER_TRANSACTION_FEES, bucket_for

        assert bucket_for("PerItemFee") == OTHER_TRANSACTION_FEES
        assert bucket_for("TaxCollectionFee") == OTHER_TRANSACTION_FEES

    def test_none_breakdown_type_is_unmapped(self):
        from shared.finance_buckets import UNMAPPED, bucket_for

        assert bucket_for(None) == UNMAPPED

    def test_unrecognized_breakdown_type_is_unmapped_not_other(self):
        from shared.finance_buckets import UNMAPPED, bucket_for

        assert bucket_for("SomeBrandNewChargeTypeAmazonAddsLater") == UNMAPPED


class TestBucketRows:
    def test_sums_amounts_per_bucket(self):
        from shared.finance_buckets import OTHER_TRANSACTION_FEES, PRODUCT_SALES, SELLING_FEES, bucket_rows

        rows = [
            {"breakdownType": "Principal", "breakdownAmount": 49.90},
            {"breakdownType": "Commission", "breakdownAmount": -8.48},
            {"breakdownType": "FBACustomerReturnPerUnitFee", "breakdownAmount": -2.27},
        ]

        totals = bucket_rows(rows)

        assert totals[PRODUCT_SALES] == 49.90
        assert totals[SELLING_FEES] == -8.48
        assert totals[OTHER_TRANSACTION_FEES] == -2.27

    def test_every_bucket_present_even_when_zero(self):
        from shared.finance_buckets import ALL_BUCKETS, bucket_rows

        totals = bucket_rows([])

        for bucket in ALL_BUCKETS:
            assert totals[bucket] == 0.0

    def test_rows_with_none_amount_are_skipped(self):
        from shared.finance_buckets import UNMAPPED, bucket_rows

        totals = bucket_rows([{"breakdownType": None, "breakdownAmount": None}])

        assert totals[UNMAPPED] == 0.0
