# Selectors — localhost

多数元素已在 sop_md 用稳定 id/name 描述。需指纹的补充：

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|--- |
| 网格行选中 checkbox | 每行首列 | `type=checkbox`, id 形如 `idscheckN` | id/value 即实体 id |
| 商品行 Edit 链接 | 行尾 Action 列 | `aria-label=Edit <商品全名>` | 变体靠完整名区分 |
| 评论行 Edit 链接 | 行尾 Action 列 | 可见文本"Edit"或行 `tr title=.../edit/id/N/` | id 随机 |
| 评论筛选输入 | 评论网格筛选行 | `id=reviewGrid_filter_name`/`_sku`/`_title`/`_nickname`/`_detail`/`_review_id`/`_status`/`_type` | id 稳定 |
| 评论日期筛选 | 筛选行 Created 列 | `name=created_at[from]`/`[to]`, `placeholder=mm/dd/yyyy` | id 含随机段 |
| 评论编辑页字段 | Edit Review 页 | `id=nickname`/`title`/`detail`/`status_id` | textarea detail 含完整文本 |
| 评论编辑页操作按钮 | Edit Review 页顶部 | `id=back`/`reset`/`delete`/`save_button`/`save_and_next`/`save_and_previous`/`next`/`previous` | 靠 title+id 识别 |
| 删除确认对话框 | 点击 Delete Review 后弹出 | `role=dialog`, 按钮可见文本 "Cancel"/"OK" | 按钮无稳定 id，靠可见文本 |
| 缓存刷新按钮 | Cache Management 顶部 | `id=flush_magento`/`flush_system`/`flushCatalogImages`/`flushJsCss`/`flushStaticFiles` | |
| 前台商品页标签 | 商品页中部 | `id=tab-label-description-title` 等 | |
| 商品/订单/客户 Filters 面板 | Filters 展开面板 | `name=name`/`sku`/`qty[from]`/`status`/`increment_id`/`billing_telephone` 等 | id 随机靠 name |
| 商品编辑核心字段 | label 旁 | `name=product[price]` 等 | id 随机 |
| Attribute Set 选择入口 | 新建商品页 | 可见文本"Default" | 点击后出现列表 + 搜索框 |
| Categories 选择入口 | Categories 区 | 可见文本"Select..."；树内 checkbox + "Done" | 勾选后必须点 Done |
| 变体矩阵输入 | Current Variations 表各行 | `name=configurable-matrix[N][price]` 等 | 靠行内 Attributes 列文本定位 |
| 变体行图片上传 | 矩阵每行 Image 列 | `type=file`, `name=image` | 页面底部另有 multiple 批量输入，勿混淆 |