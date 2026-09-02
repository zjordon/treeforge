# Selectors — localhost

| 元素用途 | 怎么找到它 | 稳定标识 | 备注 |
|---|---|---|---|
| 产品关键词搜索框 | Products 网格顶部过滤区 | `id=fulltext, placeholder=Search by keyword` | 先 Clear all 再搜索 |
| 父商品 Edit 链接 | 搜索结果第一行（Configurable Product, ID 78）| `aria-label=Edit Teton Pullover Hoodie, 可见文本"Edit"` | 须与变体行（如 Edit Teton Pullover Hoodie-XS-Red）区分 |
| Enable Product 开关 | 商品编辑页表单第一项 "Enable Product" 标签旁 | `name=product[status], type=checkbox` | id 每次加载会变（如 BQG6PKS），靠 name 定位 |
| 保存按钮 | 编辑页右上角 | `id=save-button, aria-label=Save, 可见文本"Save"` | 保存后可能出现 Please wait... 遮罩 |
| 返回按钮 | 编辑页右上角 Save 旁 | `id=back, title=Back, 可见文本"Back"` | 保存后返回网格验证 Status |