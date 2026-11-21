/**
 * Leoparts Ozon ST100 isolated adapter — PROBE ONLY.
 * This module is intentionally READ-ONLY. It must never be installed in production
 * without independent staging verification. No Capyflow code is copied.
 * It does not modify Tilda state, checkout, payment, shipping fields, or HEAD scripts.
 */
(function (root) {
  'use strict';
  const api = Object.freeze({
    inspect: function (doc, win) {
      const form = doc.querySelector('.t706 form');
      const scope = doc.querySelector('.t706 #customdelivery');
      const radios = scope ? Array.from(scope.querySelectorAll('input[name="tildadelivery-type"]')) : [];
      const options = radios.map(function (radio) {
        const label = radio.closest('label') || radio.parentElement;
        const title = (label ? label.textContent : radio.value || '').trim();
        return {title: title, checked: !!radio.checked, serviceIdPresent: !!radio.getAttribute('data-service-id')};
      });
      const tcart = win.tcart || {};
      return {
        cartFound: !!doc.querySelector('.t706'),
        formFound: !!form,
        advancedDeliveryFound: !!scope,
        deliveryOptions: options,
        priceUpdateFunctionAvailable: !!(win.tcart_newDelivery && typeof win.tcart_newDelivery.updatePriceValueInRadio === 'function'),
        recalcFunctionAvailable: typeof win.tcart__updateDelivery === 'function',
        productsCount: Array.isArray(tcart.products) ? tcart.products.length : null,
        // Deliberately exclude phone, email, name, addresses, IDs, prices and secrets.
        warnings: !scope ? ['ST100 advanced delivery not detected'] : []
      };
    }
  });
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.LeopartsOzonProbe = api;
})(typeof window !== 'undefined' ? window : globalThis);
