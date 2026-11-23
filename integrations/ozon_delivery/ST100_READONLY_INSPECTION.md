# Read-only ST100 inspection — developer-console procedure (staging first)

**Purpose:** collect only *structural* facts needed to choose an Ozon integration approach. Do not run on the production site until the owner explicitly approves. No installation or code changes. This procedure intentionally avoids customer data, products, amounts, API keys, cookies, and checkout actions.

## Inspection script

Paste in the browser Developer Tools Console on a **non-public staging copy** with ST100 open:

```js
(() => {
  const scope = document.querySelector('.t706 #customdelivery');
  const cart = document.querySelector('.t706');
  const radios = scope ? [...scope.querySelectorAll('input[name="tildadelivery-type"]')] : [];
  const report = {
    cartFound: !!cart,
    advancedDeliveryFound: !!scope,
    deliveryOptions: radios.map(r => ({
      title: (r.closest('label')?.textContent || r.value || '').trim().slice(0, 100),
      checked: !!r.checked,
      serviceIdPresent: !!r.getAttribute('data-service-id')
    })),
    updatePriceFunctionAvailable: typeof window.tcart_newDelivery?.updatePriceValueInRadio === 'function',
    recalcFunctionAvailable: typeof window.tcart__updateDelivery === 'function',
    checkoutFormPresent: !!document.querySelector('.t706 form'),
    deliveryTypeRadioCount: radios.length
  };
  console.log('Leoparts ST100 read-only report:', report);
  return report;
})();
```

## Privacy and safety
- Does not make network requests, install scripts, change prices, alter DOM, or submit an order.
- Labels can contain custom text; inspect before sharing to ensure no private data.
- Do not run arbitrary code from third-party websites in the browser console.
- Does not establish actual payment-total integration or Ozon API compatibility.
- Never modify production HEAD buy-to-cart script, Yandex Delivery, CDEK, or vk-proxy.

## What results mean
- `advancedDeliveryFound`: distinguishes advanced ST100 from simple list.
- `updatePriceFunctionAvailable`: whether the internal function seen in Capyflow's widget exists on this Tilda version. **Not proof of supported or safe payment update.**
- Actual payment verification still requires a staging checkout and server-side order/payment reconciliation, not console inspection.
