// Add stock directly via GitHub API
window._addStock = async function(code, name, el) {
    var toast = document.getElementById('toast');
    toast.textContent = '添加中...';
    toast.className = 'toast show';
    try {
        var u = 'https://api.github.com/repos/ljc060422/daily_stock_analysis/contents/stock_list.txt';
        var r = await fetch(u, {headers:{'Authorization':'Bearer '+GH_TOKEN,'Accept':'application/vnd.github+json'}});
        var d = await r.json();
        var txt = atob(d.content);
        var lines = txt.split('\n').map(function(l){return l.trim();}).filter(function(l){return /^[0-9]{6}$/.test(l);});
        if (lines.indexOf(code) >= 0) {
            toast.textContent = name + '(' + code + ') 已在关注列表中';
            toast.className = 'toast show';
        } else {
            lines.push(code);
            var nc = lines.join('\n') + '\n';
            var pr = await fetch(u, {
                method: 'PUT',
                headers: {'Authorization':'Bearer '+GH_TOKEN,'Accept':'application/vnd.github+json','Content-Type':'application/json'},
                body: JSON.stringify({message: 'add ' + code + ' ' + name, content: btoa(nc), sha: d.sha})
            });
            if (pr.ok) {
                toast.textContent = '✅ ' + name + '(' + code + ') 已添加！明天日报将包含此股票';
                toast.className = 'toast success show';
                TRACKED_SET[code] = true;
                if (el) {
                    el.setAttribute('data-tracked', '1');
                    var sp = el.querySelector('span');
                    sp.textContent = '已关注';
                    sp.className = 'si-badge';
                    sp.style.cssText = '';
                }
            } else {
                throw new Error('API error');
            }
        }
    } catch(e) {
        toast.textContent = '添加失败，请稍后重试';
        toast.className = 'toast error show';
    }
    setTimeout(function(){ toast.className = 'toast'; }, 3000);
};

// Override showStockInfo to use _addStock for untracked stocks
var _origShowStockInfo = window.showStockInfo;
window.showStockInfo = function(el) {
    var name = el.getAttribute('data-name');
    var code = el.getAttribute('data-code');
    var tracked = el.getAttribute('data-tracked') === '1';
    if (tracked) {
        var d = document.getElementById('stock-' + code);
        if (d) {
            window.toggleDetail('stock-' + code);
            document.getElementById('search-results').style.display = 'none';
            document.getElementById('search-input').value = '';
            return;
        }
    }
    window._addStock(code, name, el);
};
