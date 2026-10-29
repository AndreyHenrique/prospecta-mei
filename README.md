# Prospecta MEI

Sistema local para encontrar MEIs (e ex-MEIs) por município, bairro e atividade,
usando a base pública do CNPJ da Receita Federal. Roda no próprio computador,
sem mensalidade e sem limite de consultas.

O que ele faz:

- Baixa a base oficial da Receita (atualizada todo mês) e guarda só os MEIs do Ceará.
- Filtra por município, bairro, atividade (CNAE), data de abertura, situação e contato disponível.
- Tem listas prontas para prospecção: MEIs abertos nos últimos 30 dias, empresas que
  **saíram do MEI** no último ano (viraram ME e precisam de contador), MEIs **inaptos**
  (com pendências) e MEIs com WhatsApp ainda não contatados.
- Abre o WhatsApp, o e-mail ou o mapa direto da ficha da empresa.
- Guarda o acompanhamento de cada contato (contatado, interessado, virou cliente, sem
  interesse) e anotações. Isso fica salvo mesmo quando a base é atualizada.
- Exporta a lista filtrada para Excel ou CSV.

---

## Instalação no Windows (uma vez só)

1. **Instale o Python** (versão 3.11 ou mais nova) em <https://www.python.org/downloads/>.
   Na primeira tela do instalador, marque **"Add python.exe to PATH"**.
2. **Descompacte** a pasta `prospecta-mei` num lugar fixo, por exemplo `Documentos`.
   Deixe pelo menos **10 GB livres** no disco.
3. Dê dois cliques em **`Abrir Prospecta MEI.bat`**. Na primeira vez ele instala os
   componentes (1 a 2 minutos) e depois abre o sistema no navegador.
4. Na tela inicial, clique em **Baixar base agora**. São cerca de 7 GB; com internet
   comum leva de 30 minutos a algumas horas. Pode deixar rodando: se a internet cair,
   ele continua de onde parou.

Dica: crie um atalho do `Abrir Prospecta MEI.bat` na Área de Trabalho.

A janela preta que abre junto é o "motor" do sistema. Enquanto estiver usando,
deixe ela aberta (pode minimizar). Para fechar o sistema, feche essa janela.

No Linux ou macOS, use `./abrir.sh` no lugar do `.bat`.

## Uso no dia a dia

- **Comece pelo local.** Escolha o município e, se quiser, os bairros. Sem município,
  a busca vale para o estado todo.
- **Use as listas prontas** no topo. Elas mantêm o município, o bairro e a atividade
  que você escolheu e trocam o resto dos filtros.
- **Clique numa empresa** para abrir a ficha completa, no formato do cartão CNPJ.
  Ali tem os botões de WhatsApp, e-mail, mapa e o seu acompanhamento.
- **Panorama:** mostra onde estão e o que fazem as empresas filtradas. Clicar num item
  adiciona aquele filtro.
- **Baixar planilha Excel** leva até 200 mil linhas; para mais que isso use o CSV.

Os filtros ficam lembrados de uma vez para outra.

## Atualizar a base todo mês

A Receita publica uma base nova por mês (geralmente na primeira quinzena). Clique em
**Atualizar base** no canto superior direito. O sistema confere se existe base nova,
baixa e troca sozinho. Dá para continuar usando enquanto isso.

## Bom saber sobre os dados

- A base é **mensal** e chega com algumas semanas de atraso em relação ao cadastro real.
- Telefone e e-mail são os que o empreendedor informou à Receita. Podem estar
  desatualizados ou ser do escritório que abriu o MEI.
- O nome empresarial do MEI vem da Receita com o CPF do titular no final. O sistema
  **remove o CPF** e não o guarda.
- Por padrão, são guardadas empresas ativas, inaptas e suspensas. As baixadas ficam de
  fora para a base ficar pequena (use `--incluir-baixadas` se precisar delas).
- Os dados são públicos, mas o contato com pessoas deve seguir a LGPD: apresente-se,
  diga como conseguiu o contato se perguntarem e respeite quem pedir para não receber
  mensagens. Não revenda a lista.

---

## Para quem mantém o sistema

```
prospecta-mei/
├── Abrir Prospecta MEI.bat   # instala na 1ª vez e abre (Windows)
├── abrir.sh                  # idem para Linux/macOS
├── app.py                    # servidor FastAPI (http://localhost:8765)
├── atualizar_base.py         # download + filtro MEI/UF + montagem do SQLite
├── static/index.html         # interface (HTML/CSS/JS puro, funciona offline)
├── ferramentas/gerar_demo.py # gera zips fictícios no layout da Receita
└── dados/                    # criado na execução
    ├── mei.db                # base de consulta (~300–500 MB para o CE)
    ├── acompanhamento.db     # status e anotações (não é apagado nas atualizações)
    └── zips/AAAA-MM/         # arquivos da Receita do mês atual (~7 GB)
```

### Como a importação funciona

1. Descobre o mês mais recente pelo WebDAV público da Receita
   (`arquivos.receitafederal.gov.br/public.php/dav/files/YggdBLfdninEJX9/`). Se a Receita
   não responder, usa o espelho gratuito da Casa dos Dados. Os endereços ficam no topo de
   `atualizar_base.py`.
2. Baixa 23 arquivos (Estabelecimentos 0–9, Empresas 0–9, Simples, Cnaes, Municipios), com
   retomada (HTTP Range) e verificação de tamanho. Sócios não são necessários.
3. Lê os CSV direto de dentro dos zips, em streaming:
   Estabelecimentos filtrados pela UF → Simples (`opcao_mei = S` vira **MEI**; quem tem data de
   opção ou exclusão do MEI mas não é mais MEI vira **EX-MEI**) → Empresas (razão social).
4. Monta `mei_novo.db` com índices e troca pelo `mei.db`. Se o sistema estiver aberto
   no Windows e a troca falhar, o próprio servidor troca na próxima consulta.

Suporta o CNPJ alfanumérico (emitido desde julho de 2026). Todo CNPJ é tratado como texto.

### Comandos úteis

```bash
python atualizar_base.py                       # mês mais recente, Ceará
python atualizar_base.py --uf CE,PI,RN         # outros estados
python atualizar_base.py --mes 2026-09         # mês específico
python atualizar_base.py --fonte espelho       # força o espelho da Casa dos Dados
python atualizar_base.py --somente-importar --pasta-zips "D:/cnpj/2026-10"   # zips já baixados
python atualizar_base.py --apagar-zips         # libera ~7 GB depois de importar
```

Pelo botão da interface, o sistema reaproveita os estados já importados. Para mudar de
estado, rode uma vez pelo terminal com `--uf`.

Para atualizar sozinho todo mês, agende no Agendador de Tarefas do Windows:
`<pasta>\.venv\Scripts\python.exe <pasta>\atualizar_base.py` (ex.: dia 20 de cada mês).

### Testar sem baixar a base real

```bash
python ferramentas/gerar_demo.py
python atualizar_base.py --somente-importar --pasta-zips dados/demo_zips --mes 2026-10
python app.py
```

Os dados de demonstração são inventados. Apague `dados/mei.db` antes de baixar a base real.
