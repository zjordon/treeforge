# Disable Teton Pullover Hoodie (Magento Admin)

Task: turn OFF the product's Enable Product status so it's disabled site-wide.

1. Navigate to `http://localhost:7780/admin/admin/dashboard/` (already logged in as admin).
2. Click `Catalog` in the left admin menu (`li id=menu-magento-catalog-catalog`, visible text "Catalog"), then click `Products` in the expanded submenu.
3. On the Products grid, clear any active filters first: click the `Clear all` button (visible text "Clear all") so the keyword filter is empty and all 2040 records show.
4. In the keyword search box (`input id=fulltext`, placeholder "Search by keyword"), type the product name `Teton Pullover Hoodie`, then click the `Search` button (`button aria-label=Search`). The grid reloads with 16 records: the configurable parent "Teton Pullover Hoodie" (ID 78, SKU MH02) plus 15 simple variants.
5. Scroll down 1–2 viewport heights if needed and click the Edit link of the FIRST row — the configurable parent, `a aria-label=Edit Teton Pullover Hoodie` (visible text "Edit"). Do NOT pick a variant row (e.g. `Edit Teton Pullover Hoodie-XS-Red`); disabling the parent covers the whole product.
6. On the product edit page, locate the "Enable Product" checkbox: `input type=checkbox name=product[status]` (id varies, e.g. BQG6PKS). It is currently `checked=true` (enabled). Click its `<label>` (or the checkbox) to uncheck it → `checked=false`, value=2 (disabled).
7. Click `Save` (`button id=save-button`, aria-label=Save). A "Please wait..." overlay may appear briefly while the page saves.
8. Click `Back` (`button id=back`) to return to the Products grid and verify the parent row's Status column shows "Disabled".

Done — success when the Teton Pullover Hoodie parent row shows Status = Disabled.