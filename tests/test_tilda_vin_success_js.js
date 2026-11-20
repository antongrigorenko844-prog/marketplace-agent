/* No network: simulate the published Tilda form and its success event. */
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
const html=fs.readFileSync("data/direct_optimization_20261120/TILDA_VIN_SUCCESS_SNIPPET.html","utf8");
const code=(html.match(/<script>([\s\S]*?)<\/script>/i)||[])[1];
assert.ok(code,"snippet must contain JavaScript");
function makeTest(opts={}) {
  let time=2000;
  const hits=[],listeners={};
  const form={id:"form5246286101",dataset:{},
     addEventListener(event,handler){
       (listeners[event] ||= []).push(handler);
     }};
  const win={ym:(...args)=>hits.push(args)};
  const document={
    readyState:"complete",
    getElementById:id=>id==="form5246286101"&&!opts.missing ? form : null,
    documentElement:{},
    addEventListener:(event,fn)=>{listeners[event]=[fn];}
  };
  const sandbox={document,window:win,
     Date:{now:()=>time},
     MutationObserver:class {
        constructor(fn){this.cb=fn;}
        observe(){this.observing=true;}
        disconnect(){this.observing=false;}
     }};
  vm.runInNewContext(code,sandbox);
  return {timeTo:n=>{time=n},hits,listeners,form,runAgain:()=>vm.runInNewContext(code,sandbox)};
}
const t=makeTest();
assert.strictEqual(t.hits.length,0,"page loading must not record a goal");
assert.strictEqual((t.listeners["submit"]||[]).length,0,"submit attempts must not be hooked");
assert.strictEqual((t.listeners["click"]||[]).length,0,"clicks must not be hooked");
assert.strictEqual((t.listeners["tildaform:aftersuccess"]||[]).length,1);
t.listeners["tildaform:aftersuccess"][0]();
assert.strictEqual(t.hits.length,1);
assert.deepStrictEqual(t.hits[0],[113839860,"reachGoal","LEOPARTS_VIN_SUCCESS_5246286101"]);
t.timeTo(2800);
t.listeners["tildaform:aftersuccess"][0]();
assert.strictEqual(t.hits.length,1,"duplicate event within 1.5 s must be suppressed");
t.timeTo(5000);
t.listeners["tildaform:aftersuccess"][0]();
assert.strictEqual(t.hits.length,2,"later successful submission is another event");
t.runAgain();
assert.strictEqual(t.listeners["tildaform:aftersuccess"].length,1,"no duplicate listener on republish");
const absent=makeTest({missing:true});
assert.strictEqual(absent.hits.length,0);
assert.ok(!absent.listeners["tildaform:aftersuccess"],"other pages must not be tracked");
console.log("VIN success-only Tilda JS snippet: 8 guards passed");
