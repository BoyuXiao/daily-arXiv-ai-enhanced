"""Keep listing metadata; missing fields are resolved in batches after crawling."""


class DailyArxivPipeline:
    def process_item(self, item: dict, spider):
        item["pdf"] = f"https://arxiv.org/pdf/{item['id']}"
        item["abs"] = f"https://arxiv.org/abs/{item['id']}"
        return item
