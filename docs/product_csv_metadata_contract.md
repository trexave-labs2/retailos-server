# Product CSV metadata contract

This contract applies to the authenticated `POST /product/import` endpoint. Existing required columns `name` and `price` remain required. CSV imports continue to process rows independently and report row-level failures; a bad row must not prevent unrelated valid rows from importing.

## Supported columns

| Column | Required | Contract |
|---|---|---|
| `name` | Yes | Non-empty product or service name. |
| `price` | Yes | Selling price, non-negative, maximum two decimal places. |
| `sku` | No | Optional stock-keeping identifier, trimmed, maximum 64 characters. Unique case-insensitively within a store. Blank means absent. |
| `barcode` | No | Optional barcode stored as text, trimmed, maximum 128 characters. Leading zeros are significant. Unique within a store. Blank means absent. |
| `category` | No | Existing category name, matched case-insensitively within the importing store and the row's `product_type`. Unknown or ambiguous categories fail that row. Categories are not created implicitly. |
| `category_id` | No | Legacy alternative to `category`; an integer category ID belonging to the same store and matching `product_type`. Do not supply both category fields. |
| `cost_price` | No | Optional acquisition/unit cost, non-negative, maximum two decimal places. Blank means unknown, not zero. |
| `product_type` | No | `Physical` or `Service`; defaults to `Physical`. |
| `base_unit` | No | Defaults to `piece`. |
| `opening_stock` | No | Non-negative integer; defaults to zero. |
| `low_stock_threshold` | No | Optional positive integer. Services cannot have stock or a threshold. |

Metadata identifiers are not numeric values. SKU and barcode are retained as strings so values such as `000123` are not altered. Values are scoped to a store, not globally unique across RetailOS.

## Validation and persistence

- Unknown columns and duplicate/empty headers reject the file, using the existing CSV import error response.
- Invalid metadata fails only its row and appears in the `errors` list with the physical CSV row number and field name.
- SKU and barcode uniqueness are enforced both during import validation and by database unique indexes to protect concurrent requests.
- Category names resolve to existing categories for the current store and product type. The stored relationship remains `category_id`; the supplied name is not duplicated in product records.
- `cost_price` is nullable and persisted as a two-decimal monetary value. It is distinct from selling `price` and must never be used as the sale price.
- Existing products receive null metadata values during migration. No values are guessed or backfilled.
- Existing `category_id` imports remain supported for backwards compatibility. Supplying both `category` and `category_id` is invalid.
- The import endpoint continues to require session authentication and store authorization.

## Deliberate scope

This change defines and persists metadata through CSV import and the product database model. CSV export and general product create/update API payloads are separate capabilities and are not implied by this import contract.
