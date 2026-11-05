// Coloca o painel admin (lejianwen/rustdesk-api-web v2.7, já compilado em ../admin-ui)
// em português do Brasil: adiciona o idioma pt-BR, deixa-o como padrão e troca os
// textos fixos em chinês. Pode ser rodado de novo: não aplica duas vezes.
//
// Uso: node admin-i18n/patch-admin-ui.js [pasta-admin-ui]
'use strict';
const fs = require('fs');
const path = require('path');

const dir = path.resolve(process.argv[2] || path.join(__dirname, '..', 'admin-ui'));
const MARK = '/*itldesk-pt-BR*/';

const pt = JSON.parse(fs.readFileSync(path.join(__dirname, 'pt-BR.json'), 'utf8'));
const dict = {};
for (const [k, v] of Object.entries(pt)) {
  const [one, other] = v.split(' ||| ');
  dict[k] = other ? { One: one, Other: other } : { One: one };
}

// textos do Element Plus (paginação, tabelas, datas, diálogos)
const elementPt = {
  name: 'pt-br',
  el: {
    breadcrumb: { label: 'Navegação' },
    colorpicker: { confirm: 'Confirmar', clear: 'Limpar' },
    datepicker: {
      now: 'Agora', today: 'Hoje', cancel: 'Cancelar', clear: 'Limpar', confirm: 'Confirmar',
      selectDate: 'Selecione a data', selectTime: 'Selecione a hora', startDate: 'Data inicial',
      startTime: 'Hora inicial', endDate: 'Data final', endTime: 'Hora final',
      prevYear: 'Ano anterior', nextYear: 'Próximo ano', prevMonth: 'Mês anterior', nextMonth: 'Próximo mês',
      year: '', month1: 'Janeiro', month2: 'Fevereiro', month3: 'Março', month4: 'Abril', month5: 'Maio',
      month6: 'Junho', month7: 'Julho', month8: 'Agosto', month9: 'Setembro', month10: 'Outubro',
      month11: 'Novembro', month12: 'Dezembro',
      weeks: { sun: 'Dom', mon: 'Seg', tue: 'Ter', wed: 'Qua', thu: 'Qui', fri: 'Sex', sat: 'Sáb' },
      months: { jan: 'Jan', feb: 'Fev', mar: 'Mar', apr: 'Abr', may: 'Mai', jun: 'Jun', jul: 'Jul',
        aug: 'Ago', sep: 'Set', oct: 'Out', nov: 'Nov', dec: 'Dez' },
    },
    select: { loading: 'Carregando', noMatch: 'Nada encontrado', noData: 'Sem dados', placeholder: 'Selecione' },
    cascader: { noMatch: 'Nada encontrado', loading: 'Carregando', placeholder: 'Selecione', noData: 'Sem dados' },
    pagination: { goto: 'Ir para', pagesize: '/página', total: 'Total: {total}', pageClassifier: '' },
    messagebox: { title: 'Mensagem', confirm: 'Confirmar', cancel: 'Cancelar', error: 'Valor inválido' },
    upload: { deleteTip: 'tecle Delete para remover', delete: 'Excluir', preview: 'Visualizar', continue: 'Continuar' },
    table: { emptyText: 'Sem dados', confirmFilter: 'Confirmar', resetFilter: 'Limpar', clearFilter: 'Todos', sumText: 'Total' },
    tree: { emptyText: 'Sem dados' },
    transfer: { noMatch: 'Nada encontrado', noData: 'Sem dados', titles: ['Lista 1', 'Lista 2'],
      filterPlaceholder: 'Filtrar', noCheckedFormat: '{total} itens', hasCheckedFormat: '{checked}/{total} selecionados' },
    image: { error: 'FALHOU' },
    pageHeader: { title: 'Voltar' },
    popconfirm: { confirmButtonText: 'Sim', cancelButtonText: 'Não' },
    empty: { description: 'Sem dados' },
  },
};

function replaceOnce(text, from, to, file, all) {
  const n = text.split(from).length - 1;
  if (all ? n < 1 : n !== 1) throw new Error(`${file}: esperado 1 ocorrência de ${JSON.stringify(from)}, achei ${n}`);
  return text.split(from).join(to);
}

function patchFile(rel, edits) {
  const file = path.join(dir, rel);
  let text = fs.readFileSync(file, 'utf8');
  if (text.includes(MARK)) {
    console.log('já traduzido:', rel);
    return;
  }
  for (const [from, to, all] of edits) text = replaceOnce(text, from, to, rel, all);
  fs.writeFileSync(file, MARK + text);
  console.log('traduzido:', rel);
}

function findFile(sub, needle, done) {
  const folder = path.join(dir, 'static', sub);
  const hit = fs.readdirSync(folder).find((f) => f.endsWith('.js') && [needle].concat(done || []).some((n) => fs.readFileSync(path.join(folder, f), 'utf8').includes(n)));
  if (!hit) throw new Error(`não achei ${JSON.stringify(needle)} em static/${sub}`);
  return path.join('static', sub, hit);
}

const merge = '(function(b,o){function m(a,c){var r=Array.isArray(a)?a.slice():Object.assign({},a);' +
  'for(var k in c)r[k]=(c[k]&&typeof c[k]=="object"&&!Array.isArray(c[k])&&a&&a[k])?m(a[k],c[k]):c[k];return r}return m(b,o)})';

// bundle principal: idioma pt-BR, padrão pt-BR
const entry = findFile('entry', 'IS={en:ys,', ['IS={"pt-BR":']);
patchFile(entry, [
  ['IS={en:ys,', `IS={"pt-BR":${JSON.stringify(dict)},en:ys,`],
  ['const g={"zh-CN":', `const g={"pt-BR":{name:"Português (Brasil)",value:${merge}(Z,${JSON.stringify(elementPt)}),sideBarWidth:"230px"},"zh-CN":`],
  ['S=localStorage.getItem("lang")||navigator.language||"zh-CN"',
    'S=(function(){try{if(localStorage.getItem("itl_lang")!=="1"){localStorage.setItem("itl_lang","1");localStorage.setItem("lang","pt-BR")}return localStorage.getItem("lang")||"pt-BR"}catch(e){return"pt-BR"}})()'],
]);

// textos fixos em chinês
const control = findFile('chunk', '多个IP以 | 分割', ['Separe vários IPs com |']);
patchFile(control, [
  ['"多个IP以 | 分割"', '"Separe vários IPs com |"', true],
  ['", 全部填 "', '"; para todos, use "', true],
]);
const tags = findFile('chunk', '请选择颜色', ['"Escolha uma cor"']);
patchFile(tags, [['"请选择颜色"', '"Escolha uma cor"']]);

// <html lang>
const indexHtml = path.join(dir, 'index.html');
const html = fs.readFileSync(indexHtml, 'utf8');
if (html.includes('<html lang="en">')) {
  fs.writeFileSync(indexHtml, html.replace('<html lang="en">', '<html lang="pt-BR">'));
  console.log('index.html: lang=pt-BR');
}
