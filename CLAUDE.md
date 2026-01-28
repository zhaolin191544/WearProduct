# bug修改
## 我在普通搜索一个数据桶时（有2种品类的箱子，总共46条数据）
## 因为我输入了材料所属于的箱子，因此他会调用plans = search_bucket_all_plans这个方法，但是跑了很久都没有出结果
## 然后我把这些材料所属于的武器箱都删除了，此时去调用了search_bucket_all_plans_with_crate_ratio1这个方法，很快就出了结果
### 请帮我修复这个问题