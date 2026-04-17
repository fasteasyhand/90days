/**
 * Cloudflare Worker — 90-Day Report Service (ChillPay QR PromptPay)
 *
 * Endpoints:
 *   POST /          → สร้าง QR Payment (รับ JSON: { OrderNo, Amount, PhoneNumber, CustomerId })
 *   POST /notify    → รับ callback จาก ChillPay → forward ไป FastAPI
 *   GET  /return    → redirect กลับเว็บหลังจ่าย
 */

// ===== CONFIG — อ่านจาก Cloudflare Worker Environment Variables =====
// MERCHANT_CODE, API_KEY, MD5_KEY ยังคงอยู่ที่นี่ (หรือย้ายไป env ก็ได้)
const MERCHANT_CODE = "M038180";
const API_KEY       = "VqhJUMctBETtayycngpdRlZQJz6wy6RSUar2TPH0sTd1DCCViOjspbxwUzNZZB5s";
const MD5_KEY       = "zu4U9Dh3Pu8w2q2518fQy1ZkAIrWNrYfC6iacblxJ2e07deq1oMZ9j34hw5NfJXo6BzHJuvuDrL4ZeTvhIg1SteSxd8JklAVXEAlaQ12pPNG2ZYIxJxkUotudwHNi26OjrIJqsPebQrbug5DfrY3olcuE73CB5BgIl7bv";

// sandbox → production ให้เปลี่ยนเป็น https://appsrv2.chillpay.co/api/v2/Payment/
const CHILLPAY_URL = "https://sandbox-appsrv2.chillpay.co/api/v2/Payment/";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

// =========================================================================

export default {
  async fetch(request, env, ctx) {
    // อ่าน URL จาก env (ตั้งใน Cloudflare Dashboard → Settings → Variables)
    const FASTAPI_NOTIFY_URL = "https://foraminiferal-undoctrinally-jovanni.ngrok-free.dev/payment/webhook/chillpay";
    const RETURN_URL = (env.RETURN_URL || "").trim() ||
      "https://foraminiferal-undoctrinally-jovanni.ngrok-free.dev/worker/dashboard";

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: CORS });
    }

    const url = new URL(request.url);

    // ── GET /return — redirect กลับเว็บ ──────────────────────────────────
    if (url.pathname === "/return") {
      return Response.redirect(RETURN_URL, 302);
    }

    // ── POST /notify — รับ callback จาก ChillPay ─────────────────────────
    if (url.pathname === "/notify" && request.method === "POST") {
      const formData = await request.formData();
      const OrderNo       = formData.get("OrderNo")       || "";
      const PaymentStatus = formData.get("PaymentStatus") || "";
      const TransactionId = formData.get("TransactionId") || "";

      console.log("ChillPay notify → OrderNo:", OrderNo, "| Status:", PaymentStatus);

      // Forward ไป FastAPI
      await fetch(FASTAPI_NOTIFY_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          order_id:       OrderNo,
          status:         PaymentStatus === "0" ? "success" : "failed",
          transaction_id: TransactionId,
          signature:      "",   // ไม่ใช้ signature ตรงนี้ (trusted internal)
        }),
      }).catch(e => console.error("Forward error:", e.message));

      return new Response("OK", { status: 200 });
    }

    // ── POST / — สร้าง QR Payment ─────────────────────────────────────────
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405, headers: CORS });
    }

    try {
      const body = await request.json();

      const OrderNo    = body.OrderNo    || body.order_no    || "";
      const CustomerId = body.CustomerId || body.customer_id || "guest";
      const IPAddress  = body.IPAddress  || body.ip_address  || "127.0.0.1";
      const AmountInt  = parseInt(body.Amount || body.amount || "30000");  // satang (300 THB = 30000)

      let PhoneNumber = body.PhoneNumber || body.phone || "0812345678";
      if (!/^[0-9]{9,10}$/.test(PhoneNumber)) PhoneNumber = "0812345678";

      const Amount      = AmountInt.toString();
      const Description = (body.Description || "90day report").replace(/[^\x00-\x7F]/g, "");  // ASCII only
      const ChannelCode = "bank_qrcode";
      const Currency    = "764";
      const LangCode    = "TH";
      const RouteNo     = "1";

      // CheckSum = MD5(fields + MD5Key) — ต้องมี 8 empty strings ตาม ChillPay spec
      const rawStr = MERCHANT_CODE + OrderNo + CustomerId + Amount + PhoneNumber +
        Description + ChannelCode + Currency + LangCode + RouteNo +
        IPAddress + API_KEY +
        "" + "" + "" + "" + "" + "" + "" + "" + MD5_KEY;
      const checksum = md5(rawStr);

      const params = new URLSearchParams({
        MerchantCode: MERCHANT_CODE,
        OrderNo, CustomerId, Amount, PhoneNumber,
        Description, ChannelCode, Currency, LangCode, RouteNo,
        IPAddress,
        ApiKey:    API_KEY,
        CheckSum:  checksum,
        ReturnUrl: RETURN_URL,
      });

      const chillResp = await fetch(CHILLPAY_URL, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: params.toString(),
      });

      const result = await chillResp.json();
      console.log("ChillPay response:", JSON.stringify(result));

      // ดึง QR data จาก response
      // ChillPay คืน QRCode หรือ PaymentUrl ขึ้นอยู่กับ channel
      return new Response(JSON.stringify({
        order_id:   OrderNo,
        qr_data:    result.QRCode || result.PaymentUrl || result.qrCode || "",
        pay_url:    result.PaymentUrl || "",
        raw:        result,
      }), {
        headers: { ...CORS, "Content-Type": "application/json" },
      });

    } catch (e) {
      return new Response(JSON.stringify({ error: e.message }), {
        status: 400,
        headers: { ...CORS, "Content-Type": "application/json" },
      });
    }
  },
};

// ── MD5 ──────────────────────────────────────────────────────────────────
function md5(string) {
  function safeAdd(x, y) { var lsw=(x&0xffff)+(y&0xffff); var msw=(x>>16)+(y>>16)+(lsw>>16); return (msw<<16)|(lsw&0xffff); }
  function bitRotateLeft(num, cnt) { return (num << cnt) | (num >>> (32 - cnt)); }
  function md5cmn(q, a, b, x, s, t) { return safeAdd(bitRotateLeft(safeAdd(safeAdd(a, q), safeAdd(x, t)), s), b); }
  function md5ff(a,b,c,d,x,s,t){return md5cmn((b&c)|(~b&d),a,b,x,s,t);}
  function md5gg(a,b,c,d,x,s,t){return md5cmn((b&d)|(c&~d),a,b,x,s,t);}
  function md5hh(a,b,c,d,x,s,t){return md5cmn(b^c^d,a,b,x,s,t);}
  function md5ii(a,b,c,d,x,s,t){return md5cmn(c^(b|~d),a,b,x,s,t);}
  function str2blks(str){var nblk=((str.length+8)>>6)+1;var blks=new Array(nblk*16).fill(0);for(var i=0;i<str.length;i++){blks[i>>2]|=str.charCodeAt(i)<<((i%4)*8);}blks[str.length>>2]|=0x80<<((str.length%4)*8);blks[nblk*16-2]=str.length*8;return blks;}
  function rhex(n){var s='';for(var j=0;j<4;j++){s+=('0'+((n>>>(j*8))&0xff).toString(16)).slice(-2);}return s;}
  var utf8=unescape(encodeURIComponent(string));
  var x=str2blks(utf8);
  var a=1732584193,b=-271733879,c=-1732584194,d=271733878;
  for(var i=0;i<x.length;i+=16){
    var oa=a,ob=b,oc=c,od=d;
    a=md5ff(a,b,c,d,x[i],7,-680876936);d=md5ff(d,a,b,c,x[i+1],12,-389564586);c=md5ff(c,d,a,b,x[i+2],17,606105819);b=md5ff(b,c,d,a,x[i+3],22,-1044525330);
    a=md5ff(a,b,c,d,x[i+4],7,-176418897);d=md5ff(d,a,b,c,x[i+5],12,1200080426);c=md5ff(c,d,a,b,x[i+6],17,-1473231341);b=md5ff(b,c,d,a,x[i+7],22,-45705983);
    a=md5ff(a,b,c,d,x[i+8],7,1770035416);d=md5ff(d,a,b,c,x[i+9],12,-1958414417);c=md5ff(c,d,a,b,x[i+10],17,-42063);b=md5ff(b,c,d,a,x[i+11],22,-1990404162);
    a=md5ff(a,b,c,d,x[i+12],7,1804603682);d=md5ff(d,a,b,c,x[i+13],12,-40341101);c=md5ff(c,d,a,b,x[i+14],17,-1502002290);b=md5ff(b,c,d,a,x[i+15],22,1236535329);
    a=md5gg(a,b,c,d,x[i+1],5,-165796510);d=md5gg(d,a,b,c,x[i+6],9,-1069501632);c=md5gg(c,d,a,b,x[i+11],14,643717713);b=md5gg(b,c,d,a,x[i],20,-373897302);
    a=md5gg(a,b,c,d,x[i+5],5,-701558691);d=md5gg(d,a,b,c,x[i+10],9,38016083);c=md5gg(c,d,a,b,x[i+15],14,-660478335);b=md5gg(b,c,d,a,x[i+4],20,-405537848);
    a=md5gg(a,b,c,d,x[i+9],5,568446438);d=md5gg(d,a,b,c,x[i+14],9,-1019803690);c=md5gg(c,d,a,b,x[i+3],14,-187363961);b=md5gg(b,c,d,a,x[i+8],20,1163531501);
    a=md5gg(a,b,c,d,x[i+13],5,-1444681467);d=md5gg(d,a,b,c,x[i+2],9,-51403784);c=md5gg(c,d,a,b,x[i+7],14,1735328473);b=md5gg(b,c,d,a,x[i+12],20,-1926607734);
    a=md5hh(a,b,c,d,x[i+5],4,-378558);d=md5hh(d,a,b,c,x[i+8],11,-2022574463);c=md5hh(c,d,a,b,x[i+11],16,1839030562);b=md5hh(b,c,d,a,x[i+14],23,-35309556);
    a=md5hh(a,b,c,d,x[i+1],4,-1530992060);d=md5hh(d,a,b,c,x[i+4],11,1272893353);c=md5hh(c,d,a,b,x[i+7],16,-155497632);b=md5hh(b,c,d,a,x[i+10],23,-1094730640);
    a=md5hh(a,b,c,d,x[i+13],4,681279174);d=md5hh(d,a,b,c,x[i],11,-358537222);c=md5hh(c,d,a,b,x[i+3],16,-722521979);b=md5hh(b,c,d,a,x[i+6],23,76029189);
    a=md5hh(a,b,c,d,x[i+9],4,-640364487);d=md5hh(d,a,b,c,x[i+12],11,-421815835);c=md5hh(c,d,a,b,x[i+15],16,530742520);b=md5hh(b,c,d,a,x[i+2],23,-995338651);
    a=md5ii(a,b,c,d,x[i],6,-198630844);d=md5ii(d,a,b,c,x[i+7],10,1126891415);c=md5ii(c,d,a,b,x[i+14],15,-1416354905);b=md5ii(b,c,d,a,x[i+5],21,-57434055);
    a=md5ii(a,b,c,d,x[i+12],6,1700485571);d=md5ii(d,a,b,c,x[i+3],10,-1894986606);c=md5ii(c,d,a,b,x[i+10],15,-1051523);b=md5ii(b,c,d,a,x[i+1],21,-2054922799);
    a=md5ii(a,b,c,d,x[i+8],6,1873313359);d=md5ii(d,a,b,c,x[i+15],10,-30611744);c=md5ii(c,d,a,b,x[i+6],15,-1560198380);b=md5ii(b,c,d,a,x[i+13],21,1309151649);
    a=md5ii(a,b,c,d,x[i+4],6,-145523070);d=md5ii(d,a,b,c,x[i+11],10,-1120210379);c=md5ii(c,d,a,b,x[i+2],15,718787259);b=md5ii(b,c,d,a,x[i+9],21,-343485551);
    a=safeAdd(a,oa);b=safeAdd(b,ob);c=safeAdd(c,oc);d=safeAdd(d,od);
  }
  return rhex(a)+rhex(b)+rhex(c)+rhex(d);
}
