"""Fix JS block in report_to_html.py to avoid backslash escaping issues."""
import re

with open('report_to_html.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the JS = ''' ... ''' block
start = content.find("JS = '''")
if start == -1:
    print("JS block not found")
    exit(1)

# Find the closing '''
end = content.find("\n'''", start + 10)
if end == -1:
    # Try to find just '''
    end = content.find("'''", start + 10)
if end == -1:
    print("JS end not found")
    exit(1)

end += 3  # include the closing '''

# New JS block
new_js = """JS = '''(function() {
    var TRACKED_SET = {};
    for (var i = 0; i < TRACKED_CODES.length; i++) { TRACKED_SET[TRACKED_CODES[i]] = true; }
    var stockCache = null, searchTimer = null, chartCache = {};
    var NL = String.fromCharCode(10);

    function loadStocks(cb) {
        if (stockCache) { cb(stockCache); return; }
        var xhr = new XMLHttpRequest();
        xhr.open('GET', 'stocks.json', true);
        xhr.onload = function() { cb(xhr.status === 200 ? JSON.parse(xhr.responseText) : []); };
        xhr.onerror = function() { cb([]); };
        xhr.send();
    }

    function doSearch(q) {
        var div = document.getElementById('search-results');
        if (!div) return;
        if (q.length < 1) { div.innerHTML = ''; div.style.display = 'none'; return; }
        loadStocks(function(stocks) {
            var results = [], ql = q.toLowerCase();
            for (var i = 0; i < stocks.length && results.length < 20; i++) {
                var s = stocks[i];
                if (s.c.indexOf(q) === 0 || s.n.indexOf(q) >= 0 || (s.p && s.p.indexOf(ql) >= 0))
                    results.push(s);
            }
            if (results.length === 0) {
                div.innerHTML = '<div class=\"no-results\">未找到匹配股票</div>';
            } else {
                var html = '';
                for (var j = 0; j < results.length; j++) {
                    var r = results[j], t = TRACKED_SET[r.c];
                    html += '<div class=\"search-item' + (t ? ' tracked' : '') + '\" onclick=\"window.showStockInfo(this)\" data-code=\"' + r.c + '\" data-name=\"' + r.n + '\" data-tracked=\"' + (t ? '1' : '0') + '\"><div><div>' + r.n + '</div><div class=\"si-code\">' + r.c + '</div></div>' + (t ? '<span class=\"si-badge\">已关注</span>' : '<span style=\"background:#6366f1;color:#fff;font-size:10px;padding:3px 8px;border-radius:8px\">➕ 关注</span>') + '</div>';
                }
                div.innerHTML = html;
            }
            div.style.display = 'block';
        });
    }

    window.showStockInfo = function(el) {
        var name = el.getAttribute('data-name'), code = el.getAttribute('data-code');
        if (el.getAttribute('data-tracked') === '1') {
            var d = document.getElementById('stock-' + code);
            if (d) { window.toggleDetail('stock-' + code); document.getElementById('search-results').style.display = 'none'; document.getElementById('search-input').value = ''; return; }
        }
        window._addStock(code, name, el);
    };

    window._addStock = async function(code, name, el) {
        var toast = document.getElementById('toast');
        toast.textContent = '添加中...'; toast.className = 'toast show';
        try {
            var u = 'https://api.github.com/repos/ljc060422/daily_stock_analysis/contents/stock_list.txt';
            var r = await fetch(u, {headers:{'Authorization':'Bearer '+GH_TOKEN,'Accept':'application/vnd.github+json'}});
            var d = await r.json(), txt = atob(d.content);
            var lines = txt.split(NL).map(function(l){return l.trim();}).filter(function(l){return /^[0-9]{6}$/.test(l);});
            if (lines.indexOf(code) >= 0) {
                toast.textContent = name + '(' + code + ') 已在关注列表中'; toast.className = 'toast show';
            } else {
                lines.push(code);
                var nc = lines.join(NL) + NL;
                var pr = await fetch(u, {method:'PUT', headers:{'Authorization':'Bearer '+GH_TOKEN,'Accept':'application/vnd.github+json','Content-Type':'application/json'}, body:JSON.stringify({message:'add '+code+' '+name,content:btoa(nc),sha:d.sha})});
                if (pr.ok) {
                    toast.textContent = '✅ ' + name + '(' + code + ') 已添加！明天日报将包含此股票'; toast.className = 'toast success show';
                    TRACKED_SET[code] = true;
                    if (el) { el.setAttribute('data-tracked','1'); var sp = el.querySelector('span'); sp.textContent = '已关注'; sp.className = 'si-badge'; sp.style.cssText = ''; }
                } else { throw new Error('API'); }
            }
        } catch(e) { toast.textContent = '添加失败，请稍后重试'; toast.className = 'toast error show'; }
        setTimeout(function(){ toast.className = 'toast'; }, 3000);
    };

    window.onSearchInput = function(el) { clearTimeout(searchTimer); searchTimer = setTimeout(function(){ doSearch(el.value.trim()); }, 200); };
    window.onSearchKey = function(el, evt) { if (evt.key === 'Enter') { clearTimeout(searchTimer); doSearch(el.value.trim()); } };

    function loadChart(code, type, cb) {
        var key = code + '_' + type;
        if (chartCache[key]) { cb(chartCache[key]); return; }
        var img = new Image();
        img.onload = function() { chartCache[key] = img.src; cb(img.src); };
        img.onerror = function() { cb(null); };
        img.src = 'charts/' + code + '_' + type + '.png';
    }
    function renderCharts(code) {
        var kd = document.getElementById('kline-' + code);
        if (kd && !kd.querySelector('img')) { loadChart(code, 'kline', function(u){ kd.innerHTML = u ? '<img src=\"' + u + '\" alt=\"K线\">' : '<div class=\"chart-loading\">暂无K线数据</div>'; }); }
        var idEl = document.getElementById('itraday-' + code);
        if (idEl && !idEl.querySelector('img')) { loadChart(code, 'intraday', function(u){ idEl.innerHTML = u ? '<img src=\"' + u + '\" alt=\"分时\">' : '<div class=\"chart-loading\">暂无分时数据</div>'; }); }
    }

    window.toggleDetail = function(id) {
        var el = document.getElementById(id);
        if (!el) return;
        var icon = document.getElementById('icon-' + id), code = id.replace('stock-', '');
        if (el.style.display === 'none' || el.style.display === '') {
            el.style.display = 'block'; if (icon) icon.classList.add('open');
            renderCharts(code);
            setTimeout(function(){ el.scrollIntoView({behavior:'smooth',block:'center'}); }, 100);
        } else { el.style.display = 'none'; if (icon) icon.classList.remove('open'); }
    };
})();
'''"""

content = content[:start] + new_js + content[end:]
with open('report_to_html.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Done! JS block replaced.")
