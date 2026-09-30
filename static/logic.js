(function (root) {
  var G = ['g', 'gm', 'gms', 'gram', 'grams', 'gr'];
  var KG = ['kg', 'kgs'];
  var ML = ['ml', 'mls'];
  var L = ['l', 'lt', 'ltr', 'litre', 'liter', 'litres', 'liters'];

  // "80g" -> {n:80,t:'g'}; "250 ml" -> ml; "2" -> count; "1 cup" -> {n:1,t:'u:cup'}
  function parseQty(q) {
    var m = String(q).trim().toLowerCase().match(/^(\d+(?:\.\d+)?|\.\d+)\s*([a-z]*)$/);
    if (!m) return null;
    var n = parseFloat(m[1]), u = m[2];
    if (!(n > 0)) return null;
    if (!u) return { n: n, t: 'count' };
    if (G.indexOf(u) >= 0) return { n: n, t: 'g' };
    if (KG.indexOf(u) >= 0) return { n: n * 1000, t: 'g' };
    if (ML.indexOf(u) >= 0) return { n: n, t: 'ml' };
    if (L.indexOf(u) >= 0) return { n: n * 1000, t: 'ml' };
    return { n: n, t: 'u:' + u };
  }
  function normName(s) {
    return String(s).toLowerCase().replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim().replace(/s$/, '');
  }
  function foodKey(name, t) { return normName(name) + '|' + t; }
  function basisFrom(cal, pro, n) { return { cal1: cal / n, pro1: pro / n }; }
  function scale(food, n) {
    return { cal: Math.round(food.cal1 * n), pro: Math.round(food.pro1 * n * 10) / 10 };
  }
  var API = { parseQty: parseQty, normName: normName, foodKey: foodKey, basisFrom: basisFrom, scale: scale };
  if (typeof module !== 'undefined' && module.exports) module.exports = API; else root.Logic = API;
})(typeof window !== 'undefined' ? window : this);
