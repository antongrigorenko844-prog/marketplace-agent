"""Second exact EA888 category correction. Reuse guarded migration; no other SKU."""
import ozon_category_seo_canary_20261125 as migration
migration.SKU="EA888gen3-filter-kit"
migration.ID=7088227476
migration.OLD=(17028756,971100605)
migration.NEW=(17028751,96176)
migration.NEW_TYPE="Фильтр масляный"
migration.OUT=migration.ROOT/"data/ozon_category_seo_filter_kit_20261125.json"
def live():
    a=migration.exact(migration.oz.get_product_names(offer_ids=[migration.SKU],limit=20),"attributes")
    info=migration.exact(migration.oz.get_product_info_list([migration.SKU]),"info")
    if int(a.get("id") or 0)!=migration.ID or int(info.get("id") or info.get("product_id") or 0)!=migration.ID:
        raise ValueError("product_id drift")
    if not all(k in a.get("name","").lower() for k in ("ea888","корпус","клапан","фильтр","комплект")):
        raise ValueError("title and physical identity mismatch")
    pr=migration.exact(migration.oz.get_prices_and_stocks([migration.SKU]),"price")
    stock=migration.exact(migration.oz.get_stocks([migration.SKU]),"stock")
    return a,info,pr,stock
migration.live=live
if __name__=="__main__":migration.main()
