# Ozon API access verification (no secrets in GitHub)

Official sources:
- https://seller.ozon.ru/media/news/novoe-v-ozon-dostavke-dlya-biznesa-rabotajte-s-zakazami-po-api/
- https://dev.ozon.ru/start/446-Ozon-Logistika-podkliuchenie-nastroika-i-poriadok-raboty-s-novym-funktsionalom/
- https://help-ru.tilda.cc/online-store/shipping

## Confirmed from screenshots
- Leoparts own store connected in Ozon Delivery; FBO and FBS available.
- Tilda ST100 advanced delivery enabled; Yandex and CDEK live.
- Existing Tilda HEAD Buy-click-to-cart JavaScript is protected; do not edit it.

## Owner action (in Ozon Delivery cabinet, not regular product API settings)
1. Open **Управление частными приложениями**.
2. Check whether a private application exists with **Seller-api.ozon-logistics** permission.
3. If absent, create one scoped to the minimum required logistics access.
4. Share only whether the app exists and the permission is enabled; **never share token, client secret or API key in chat or repository**.
5. Store credentials only in a secured backend secret manager when backend hosting is chosen.

## Important caveat
Ozon Seller connection status does not prove private API access. GitHub Actions is not an interactive backend. Do not promise a live map, real quote, or order creation before credentials, API method contracts and ST100 checkout integration are tested.

## ST100 compatibility
Tilda documents that advanced mode displays only services configured under site payment/delivery settings. A custom Ozon widget therefore cannot be assumed to behave as a native third carrier. Validate isolated checkout behavior and payment total reconciliation on a staging copy before production.
