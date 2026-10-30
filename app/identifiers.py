"""Extract complete explicit identifiers without inferring source facts."""
import re

def skus(value):
    # Read complete identifiers, never a known prefix of another item.
    identifier=r'[a-z0-9]+(?:[_.-][a-z0-9]+)*'
    end=r'(?![a-z0-9_-]|\.[a-z0-9])'
    explicit=re.findall(r'(?:артикул(?:ы|а)?|\bsku)\s*[:№#]?\s*('+identifier+r')'+end,value)
    numeric=re.findall(r'(?<![a-z0-9_.-])(\d{5}(?:[_.-][a-z0-9]+)*)'+end,value)
    return list(dict.fromkeys(s.upper() for s in explicit+numeric))
def warehouses(value):
    return list(dict.fromkeys(s.upper() for s in re.findall(
        r'(?<![a-z0-9_.-])(wh-[a-z0-9]+(?:[_.-][a-z0-9]+)*)(?![a-z0-9_-]|\.[a-z0-9])',value)))
