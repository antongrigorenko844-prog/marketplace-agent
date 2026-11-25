// Draft. No network, no credentials, no automatic writes to Metrica/Direct.
// Import reconcilePaidOrders from a secure, private local reconciliation environment.
export function reconcilePaidOrders(input) {
  // Input is a normalized, complete private snapshot from independently verified
  // merchant orders and provider statements/API, NOT a raw browser/webhook event.
  const {orders,payments,asOfUnix,previouslySubmittedOrderIds=[]}=input;
  if(!Array.isArray(orders)||!Array.isArray(payments)||!Array.isArray(previouslySubmittedOrderIds)||!Number.isSafeInteger(asOfUnix)||asOfUnix<=0) throw Error("Invalid normalized snapshot");
  const id=v=>typeof v==="string"&&/^[a-zA-Z0-9._:-]{1,128}$/.test(v);
  const minor=v=>Number.isSafeInteger(v)&&v>=0;
  const attr=v=>typeof v==="string"&&/^\d{1,40}$/.test(v);
  const byOrder=new Map(),bad=new Set(),byPayment=new Map(),globalIssues=[];
  for(const o of orders) {
    if(!id(o?.orderId)){globalIssues.push("INVALID_ORDER_ID");continue;}
    if(byOrder.has(o.orderId)){bad.add(o.orderId);continue;}
    byOrder.set(o.orderId,o);
  }
  for(const p of payments){
    if(!id(p?.orderId)||!id(p?.provider)||!id(p?.paymentId)){if(id(p?.orderId))bad.add(p.orderId);else globalIssues.push("INVALID_PAYMENT_ID");continue;}
    const k=p.provider+":"+p.paymentId;
    if(byPayment.has(k)){
      const before=byPayment.get(k);
      const same=["orderId","provider","paymentId","source","status","amountMinor","refundedMinor","currency","paidAtUnix"].every(f=>before[f]===p[f]);
      if(!same){bad.add(before.orderId);bad.add(p.orderId);}
      continue;
    }
    byPayment.set(k,p);
  }
  const submitted=new Set(previouslySubmittedOrderIds);
  const decisions=[],candidates=[];
  for(const [oid,o] of byOrder){
    const rows=[...byPayment.values()].filter(p=>p.orderId===oid);
    const decision={orderId:oid,status:"REVIEW_REQUIRED",confirmedAmountMinor:null};
    decisions.push(decision);
    if(bad.has(oid)){decision.status="CONFLICT";continue;}
    if(o.cancelled===true){decision.status="CANCELLED_REVIEW";continue;}
    if(o.currency!=="RUB"||!minor(o.expectedAmountMinor)||!o.expectedAmountMinor){decision.status="INVALID_ORDER_AMOUNT";continue;}
    if(!rows.length){decision.status="NO_PAYMENT_EVIDENCE";continue;}
    if(rows.some(p=>!["provider_statement","provider_verified_api"].includes(p.source)||!["PAID","UNPAID","CANCELLED","REFUNDED","PARTIAL_REFUNDED"].includes(p.status)||p.currency!==o.currency||!minor(p.amountMinor)||!minor(p.refundedMinor)||p.refundedMinor>p.amountMinor)){decision.status="UNVERIFIED_PAYMENT_SOURCE_OR_FIELDS";continue;}
    if(rows.some(p=>p.refundedMinor>0||["REFUNDED","PARTIAL_REFUNDED"].includes(p.status))){decision.status="REFUND_REVIEW";continue;}
    const paid=rows.filter(p=>p.status==="PAID");
    if(!paid.length){decision.status="NOT_PAID";continue;}
    if(paid.length!==1){decision.status="MULTIPLE_PAYMENTS_REVIEW";continue;}
    const p=paid[0];
    if(p.amountMinor!==o.expectedAmountMinor||!Number.isSafeInteger(p.paidAtUnix)||p.paidAtUnix<=0||p.paidAtUnix>asOfUnix){decision.status="AMOUNT_OR_DATE_MISMATCH";continue;}
    decision.confirmedAmountMinor=p.amountMinor;
    if(submitted.has(oid)){decision.status="PAID_ALREADY_SUBMITTED";continue;}
    if(asOfUnix-p.paidAtUnix>21*86400){decision.status="PAID_OUTSIDE_UPLOAD_WINDOW";continue;}
    // Attribution captured at checkout; no identity guessing and no double send.
    const kind=attr(o.yclid)?"YCLID":attr(o.clientId)?"CLIENT_ID":null;
    if(!kind){decision.status="PAID_NO_ATTRIBUTION_ID";continue;}
    decision.status="PAID_CANDIDATE";
    candidates.push({orderId:oid,identifierType:kind,identifier:kind==="YCLID"?o.yclid:o.clientId,Target:"LEOPARTS_PAID_ORDER_CONFIRMED",DateTime:p.paidAtUnix,Price:(p.amountMinor/100).toFixed(2),Currency:"RUB"});
  }
  const orphanPayments=[...byPayment.values()].filter(p=>!byOrder.has(p.orderId)).length;
  if(orphanPayments)globalIssues.push("ORPHAN_PAYMENT_ROWS");
  const counts={};let confirmedAmountMinor=0;
  for(const d of decisions){counts[d.status]=(counts[d.status]||0)+1;confirmedAmountMinor+=d.confirmedAmountMinor||0;}
  return {mode:"PREVIEW_ONLY_NEVER_UPLOAD",summary:{orderRows:orders.length,paymentRows:payments.length,distinctOrders:byOrder.size,counts,confirmedAmountMinor,offlineCandidates:candidates.length,orphanPayments,globalIssues},decisions,candidates};
}
