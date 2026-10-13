import json
import argparse
import os
from itertools import count
from pathlib import Path

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=str, help="Path to the jsonline file")
    args = parser.parse_args()
    data = []
    preference = os.environ.get('CATEGORIES', 'cs.LG, cs.DC, cs.AI').split(',')
    preference = list(map(lambda x: x.strip(), preference))
    def rank(cate):
        if cate in preference:
            return preference.index(cate)
        else:
            return len(preference)

    with open(args.data, "r") as f:
        for line in f:
            data.append(json.loads(line))

    categories = set([item["categories"][0] for item in data])
    template = Path(__file__).with_name("paper_template.md").read_text(encoding="utf-8")
    categories = sorted(categories, key=rank)
    cnt = {cate: 0 for cate in categories}
    for item in data:
        if item["categories"][0] not in cnt.keys():
            continue
        cnt[item["categories"][0]] += 1

    markdown = f"<div id=toc></div>\n\n# Table of Contents\n\n"
    for idx, cate in enumerate(categories):
        markdown += f"- [{cate}](#{cate}) [Total: {cnt[cate]}]\n"

    idx = count(1)
    for cate in categories:
        markdown += f"\n\n<div id='{cate}'></div>\n\n"
        markdown += f"# {cate} [[Back]](#toc)\n\n"
        papers = []
        for item in data:
            if item["categories"][0] == cate:
                # Safely access AI fields with default values
                ai_data = item.get('AI', {})
                if not ai_data or not isinstance(ai_data, dict):
                    papers.append(
                        f"### [{next(idx)}] [{item['title']}]({item['abs']})\n"
                        f"*{', '.join(item['authors'])}*\n\n"
                        f"Main category: {item['categories'][0]}\n\n"
                        f"Abstract: {item['summary']}\n"
                    )
                    continue
                
                # Check if all required AI fields are present
                required_fields = ['tldr', 'motivation', 'method', 'result', 'conclusion']
                if not all(field in ai_data for field in required_fields):
                    print(f"Skipping item '{item.get('title', 'Unknown')}' due to incomplete AI fields")
                    continue
                
                papers.append(
                    template.format(
                        title=item["title"],
                        authors=",".join(item["authors"]),
                        summary=item["summary"],
                        url=item['abs'],
                        tldr=ai_data.get('tldr', ''),
                        motivation=ai_data.get('motivation', ''),
                        method=ai_data.get('method', ''),
                        result=ai_data.get('result', ''),
                        conclusion=ai_data.get('conclusion', ''),
                        cate=item['categories'][0],
                        idx=next(idx)
                    )
                )
        markdown += "\n\n".join(papers)
    source = Path(args.data)
    output = source.with_name(source.stem.split('_AI_enhanced_')[0] + '.md')
    with output.open("w", encoding="utf-8") as f:
        f.write(markdown)
