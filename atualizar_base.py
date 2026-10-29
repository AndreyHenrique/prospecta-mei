#!/usr/bin/env python3
"""
Prospecta MEI - atualização da base de dados.

Baixa os Dados Abertos do CNPJ publicados pela Receita Federal, separa apenas
os MEIs (e ex-MEIs) dos estados escolhidos e monta um banco SQLite pequeno e
rápido, que o sistema usa para as buscas.

Exemplos:
    python atualizar_base.py                      # mês mais recente, Ceará
    python atualizar_base.py --uf CE,PI,RN        # mais de um estado
    python atualizar_base.py --mes 2026-10        # um mês específico
    python atualizar_base.py --somente-importar --pasta-zips "C:/Downloads/cnpj"
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import traceback
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

try:
    import requests
except ImportError:  # o modo --somente-importar funciona sem requests
    requests = None

BASE_DIR = Path(__file__).resolve().parent
DADOS_DIR = BASE_DIR / "dados"
BANCO = DADOS_DIR / "mei.db"
BANCO_NOVO = DADOS_DIR / "mei_novo.db"
MARCA_NOVO = DADOS_DIR / "mei_novo.ok"
STAGING = DADOS_DIR / "staging.db"
PROGRESSO = DADOS_DIR / "progresso.json"
TRAVA = DADOS_DIR / "atualizacao.lock"
PASTA_ZIPS = DADOS_DIR / "zips"

# Compartilhamento público da Receita (Nextcloud/WebDAV). Se a Receita trocar o
# endereço, basta atualizar estas duas linhas.
RECEITA_TOKEN = "YggdBLfdninEJX9"
URL_RECEITA = f"https://arquivos.receitafederal.gov.br/public.php/dav/files/{RECEITA_TOKEN}/"
# Espelho gratuito mantido pela Casa dos Dados (cópia dos mesmos arquivos).
URL_ESPELHO = "https://dados-abertos-rf-cnpj.casadosdados.com.br/arquivos/"

ARQ_ESTAB = [f"Estabelecimentos{i}.zip" for i in range(10)]
ARQ_EMPRESAS = [f"Empresas{i}.zip" for i in range(10)]
ARQUIVOS = ["Cnaes.zip", "Municipios.zip", "Simples.zip", *ARQ_ESTAB, *ARQ_EMPRESAS]

UFS_VALIDAS = set(
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
)
# 1 Nula, 2 Ativa, 3 Suspensa, 4 Inapta, 8 Baixada
SITUACOES_PADRAO = {2, 3, 4}

CABECALHOS = {"User-Agent": "ProspectaMEI/1.0 (uso local; dados abertos do CNPJ)"}

csv.field_size_limit(2**31 - 1)


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalizar(texto: str | None) -> str:
    """Maiúsculas, sem acentos e com espaços simples (para buscas)."""
    if not texto:
        return ""
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.upper().split())


def limpar(texto: str | None) -> str:
    return " ".join((texto or "").split())


def data_iso(valor: str | None) -> str | None:
    v = (valor or "").strip()
    if len(v) == 8 and v.isdigit() and v != "00000000":
        a, m, d = v[:4], v[4:6], v[6:]
        if a > "1900" and "01" <= m <= "12" and "01" <= d <= "31":
            return f"{a}-{m}-{d}"
    return None


def inteiro(valor: str | None) -> int | None:
    v = (valor or "").strip()
    return int(v) if v.isdigit() else None


def telefone(ddd: str | None, numero: str | None) -> str | None:
    ddd = re.sub(r"\D", "", ddd or "")
    num = re.sub(r"\D", "", numero or "")
    if not num or set(num) == {"0"} or len(num) < 7:
        return None
    ddd = ddd[-2:] if len(ddd) >= 2 else ""
    return ddd + num


def eh_celular(fone: str | None) -> bool:
    if not fone or len(fone) < 10:
        return False
    num = fone[2:]
    return (len(num) == 9 and num[0] == "9") or (len(num) == 8 and num[0] in "6789")


_CPF_FINAL = re.compile(r"\s+[\d\*\.\-]{11,14}\s*$")


def tirar_cpf(razao: str) -> str:
    """A razão social do MEI costuma terminar com o CPF do titular; ele não é
    necessário para prospecção, então não é guardado."""
    return _CPF_FINAL.sub("", razao).strip()


def numero_br(valor: str | None) -> float | None:
    v = (valor or "").strip().replace(".", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def tamanho_legivel(n: float) -> str:
    for unidade in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unidade}" if unidade in ("B", "KB") else f"{n:.1f} {unidade}"
        n /= 1024
    return f"{n:.1f} TB"


# --------------------------------------------------------------------------
# Progresso (lido pela interface web)
# --------------------------------------------------------------------------
class Progresso:
    def __init__(self, faixas: dict[str, tuple[float, float]]):
        self.faixas = faixas
        self.estado = {
            "status": "executando",
            "etapa": "Preparando",
            "mensagem": "",
            "detalhe": "",
            "percentual": 0.0,
            "pid": os.getpid(),
            "iniciado_em": agora(),
            "atualizado_em": agora(),
            "log": [],
        }
        self._ultimo = 0.0
        self._tty = sys.stdout.isatty()
        self._salvar(True)

    def log(self, msg: str) -> None:
        if self._tty:
            print(" " * 100, end="\r")
        linha = f"[{datetime.now():%H:%M:%S}] {msg}"
        print(linha, flush=True)
        self.estado["log"] = (self.estado["log"] + [linha])[-60:]
        self.estado["mensagem"] = msg
        self._salvar(True)

    def fase(self, nome: str, etapa: str) -> None:
        self._fase = nome
        self.estado["etapa"] = etapa
        self.estado["detalhe"] = ""
        self.avancar(0.0)

    def avancar(self, fracao: float, detalhe: str | None = None) -> None:
        ini, fim = self.faixas.get(getattr(self, "_fase", ""), (0, 0))
        self.estado["percentual"] = round(ini + (fim - ini) * max(0.0, min(1.0, fracao)), 1)
        if detalhe is not None:
            self.estado["detalhe"] = detalhe
            if self._tty:
                print(f"  {detalhe[:96]:<96}", end="\r", flush=True)
        self._salvar()

    def concluir(self, status: str, msg: str) -> None:
        self.estado["status"] = status
        if status == "concluido":
            self.estado["percentual"] = 100.0
        self.log(msg)

    def _salvar(self, forcar: bool = False) -> None:
        t = time.time()
        if not forcar and t - self._ultimo < 1.0:
            return
        self._ultimo = t
        self.estado["atualizado_em"] = agora()
        try:
            tmp = PROGRESSO.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.estado, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, PROGRESSO)
        except OSError:
            pass  # a interface pode estar lendo o arquivo neste instante


# --------------------------------------------------------------------------
# Fontes de download
# --------------------------------------------------------------------------
class FonteReceita:
    nome = "Receita Federal"

    def __init__(self, sessao):
        self.s = sessao

    def _propfind(self, url: str) -> list[tuple[str, int | None]]:
        r = self.s.request("PROPFIND", url, headers={**CABECALHOS, "Depth": "1"}, timeout=60)
        r.raise_for_status()
        raiz = ET.fromstring(r.content)
        itens = []
        for resp in raiz.iter("{DAV:}response"):
            href = resp.findtext("{DAV:}href") or ""
            tam = resp.findtext(".//{DAV:}getcontentlength")
            itens.append((unquote(href), int(tam) if tam and tam.isdigit() else None))
        return itens

    def meses(self) -> dict[str, str]:
        meses = {}
        for href, _ in self._propfind(URL_RECEITA):
            m = re.search(r"/(\d{4}-\d{2})/?$", href)
            if m:
                meses[m.group(1)] = m.group(1)
        return meses

    def arquivos(self, pasta: str) -> dict[str, int | None]:
        return {
            href.rstrip("/").rsplit("/", 1)[-1]: tam
            for href, tam in self._propfind(f"{URL_RECEITA}{pasta}/")
            if href.lower().endswith(".zip")
        }

    def url(self, pasta: str, arquivo: str) -> str:
        return f"{URL_RECEITA}{pasta}/{arquivo}"


class FonteEspelho:
    nome = "espelho Casa dos Dados"

    def __init__(self, sessao):
        self.s = sessao

    def meses(self) -> dict[str, str]:
        r = self.s.get(URL_ESPELHO, headers=CABECALHOS, timeout=60)
        r.raise_for_status()
        meses: dict[str, str] = {}
        for pasta in sorted(set(re.findall(r'href="(\d{4}-\d{2}-\d{2})/"', r.text))):
            meses[pasta[:7]] = pasta  # a última pasta do mês prevalece
        return meses

    def arquivos(self, pasta: str) -> dict[str, int | None]:
        r = self.s.get(f"{URL_ESPELHO}{pasta}/", headers=CABECALHOS, timeout=60)
        r.raise_for_status()
        return {nome: None for nome in set(re.findall(r'href="([A-Za-z]+\d?\.zip)"', r.text))}

    def url(self, pasta: str, arquivo: str) -> str:
        return f"{URL_ESPELHO}{pasta}/{arquivo}"


def baixar_arquivo(sessao, url: str, destino: Path, esperado: int | None, prog: Progresso,
                   ao_progredir, tentativas: int = 6) -> None:
    parcial = destino.with_name(destino.name + ".part")
    for tentativa in range(1, tentativas + 1):
        try:
            ja = parcial.stat().st_size if parcial.exists() else 0
            if esperado and ja == esperado:
                break
            cab = dict(CABECALHOS)
            if ja:
                cab["Range"] = f"bytes={ja}-"
            with sessao.get(url, headers=cab, stream=True, timeout=(30, 180)) as r:
                if r.status_code == 416:
                    parcial.unlink(missing_ok=True)
                    raise IOError("arquivo parcial inválido, recomeçando")
                if r.status_code == 206:
                    modo = "ab"
                else:
                    r.raise_for_status()
                    modo, ja = "wb", 0
                if "text/html" in r.headers.get("Content-Type", ""):
                    raise IOError("o servidor respondeu com uma página em vez do arquivo")
                restante = int(r.headers.get("Content-Length") or 0)
                total = esperado or (ja + restante if restante else None)
                with open(parcial, modo) as f:
                    for bloco in r.iter_content(chunk_size=1 << 20):
                        if bloco:
                            f.write(bloco)
                            ja += len(bloco)
                            ao_progredir(ja, total)
                if total and ja < total:
                    raise IOError(f"download interrompido ({tamanho_legivel(ja)} de {tamanho_legivel(total)})")
            break
        except Exception as e:  # noqa: BLE001 - qualquer falha de rede leva a nova tentativa
            if isinstance(e, requests.HTTPError) and e.response is not None:
                motivo = f"o servidor respondeu com erro {e.response.status_code}"
            elif isinstance(e, requests.RequestException):
                motivo = "a conexão caiu"
            else:
                motivo = str(e)
            if tentativa == tentativas:
                raise RuntimeError(f"não foi possível baixar {destino.name} ({motivo})") from e
            espera = min(90, 10 * tentativa)
            prog.log(f"{destino.name}: {motivo}. Continuando de onde parou em {espera}s "
                     f"(tentativa {tentativa + 1} de {tentativas}).")
            time.sleep(espera)
    if not zipfile.is_zipfile(parcial):
        parcial.unlink(missing_ok=True)
        raise RuntimeError(f"{destino.name} chegou corrompido; rode a atualização de novo")
    os.replace(parcial, destino)


def zip_ok(caminho: Path, esperado: int | None) -> bool:
    if not caminho.exists():
        return False
    if esperado and caminho.stat().st_size != esperado:
        return False
    return zipfile.is_zipfile(caminho)


def escolher_mes_e_baixar(args, prog: Progresso, mes_atual: str | None) -> tuple[str, Path, str] | None:
    if requests is None:
        raise RuntimeError("o pacote 'requests' não está instalado (pip install -r requirements.txt)")
    sessao = requests.Session()
    fontes = []
    ordem = ["receita", "espelho"] if args.fonte in ("auto", "receita") else ["espelho", "receita"]
    if args.fonte != "auto":
        ordem = ordem[:1]
    for chave in ordem:
        fonte = FonteReceita(sessao) if chave == "receita" else FonteEspelho(sessao)
        try:
            meses = fonte.meses()
            if meses:
                fontes.append((fonte, meses))
                prog.log(f"{fonte.nome}: meses disponíveis até {max(meses)}.")
        except Exception as e:  # noqa: BLE001
            prog.log(f"{fonte.nome} indisponível agora ({e.__class__.__name__}).")
    if not fontes:
        raise RuntimeError("nenhuma fonte respondeu. Verifique a internet e tente mais tarde.")

    if args.mes:
        candidatos = [args.mes]
    else:
        candidatos = sorted({m for _, ms in fontes for m in ms}, reverse=True)[:3]

    # Escolhe o mês mais recente que esteja completo em alguma fonte.
    for mes in candidatos:
        for fonte, meses in fontes:
            if mes not in meses:
                continue
            try:
                lista = fonte.arquivos(meses[mes])
            except Exception as e:  # noqa: BLE001
                prog.log(f"Não consegui listar {mes} em {fonte.nome}: {e}")
                continue
            faltando = [a for a in ARQUIVOS if a not in lista]
            if faltando:
                prog.log(f"{mes} em {fonte.nome} ainda está incompleto (faltam {len(faltando)} arquivos).")
                continue
            if mes_atual == mes and not args.forcar:
                return None
            pasta = Path(args.pasta_zips) / mes
            pasta.mkdir(parents=True, exist_ok=True)
            prog.log(f"Usando a base de {mes} ({fonte.nome}).")
            outras = [(f, ms) for f, ms in fontes if f is not fonte and mes in ms]
            baixar_mes(fonte, meses[mes], lista, outras, mes, pasta, prog)
            return mes, pasta, fonte.nome
    raise RuntimeError(f"nenhum mês completo encontrado (tentei {', '.join(candidatos)})")


def baixar_mes(fonte, pasta_remota: str, lista: dict, outras, mes: str, pasta: Path, prog: Progresso) -> None:
    prog.fase("download", "Baixando arquivos da Receita")
    tamanhos = {a: lista.get(a) for a in ARQUIVOS}
    conhecido = sum(t for t in tamanhos.values() if t)
    total_estimado = conhecido if all(tamanhos.values()) else 6.8 * 1024**3
    concluido = 0.0
    inicio = time.time()

    for i, arq in enumerate(ARQUIVOS, 1):
        destino = pasta / arq
        esperado = tamanhos.get(arq)
        if zip_ok(destino, esperado):
            concluido += destino.stat().st_size
            prog.avancar(concluido / total_estimado, f"{arq} já baixado")
            continue
        prog.log(f"Baixando {arq} ({i}/{len(ARQUIVOS)}"
                 + (f", {tamanho_legivel(esperado)})" if esperado else ")"))
        base = concluido
        marco = [time.time(), 0]

        def ao_progredir(feito, total, arq=arq, base=base):
            t = time.time()
            if t - marco[0] >= 1.0:
                vel = (feito - marco[1]) / (t - marco[0])
                marco[0], marco[1] = t, feito
                txt = f"{arq}: {tamanho_legivel(feito)}"
                if total:
                    txt += f" de {tamanho_legivel(total)}"
                txt += f" ({tamanho_legivel(vel)}/s)"
                prog.avancar((base + feito) / total_estimado, txt)

        tentativas = [(fonte, pasta_remota)] + [(f, ms[mes]) for f, ms in outras]
        for n, (f, p) in enumerate(tentativas):
            try:
                baixar_arquivo(f.s, f.url(p, arq), destino, esperado if f is fonte else None,
                               prog, ao_progredir)
                break
            except RuntimeError as e:
                if n == len(tentativas) - 1:
                    raise
                destino.with_name(destino.name + ".part").unlink(missing_ok=True)
                prog.log(f"{e}. Tentando pelo {tentativas[n + 1][0].nome}.")
        concluido += destino.stat().st_size

    dur = time.time() - inicio
    prog.log(f"Downloads concluídos em {dur / 60:.0f} min.")


# --------------------------------------------------------------------------
# Leitura dos CSV dentro dos zips
# --------------------------------------------------------------------------
class LeitorContado(io.RawIOBase):
    def __init__(self, f, ao_ler):
        self.f, self.lidos, self.ao_ler = f, 0, ao_ler

    def readable(self):
        return True

    def close(self):
        try:
            self.f.close()
        finally:
            super().close()

    def readinto(self, b):
        dados = self.f.read(len(b))
        n = len(dados)
        b[:n] = dados
        self.lidos += n
        self.ao_ler(self.lidos)
        return n


def detectar_codificacao(amostra: bytes) -> str:
    if not any(b >= 0x80 for b in amostra):
        return "latin-1"
    try:
        amostra.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError as e:
        return "utf-8" if e.start >= len(amostra) - 3 else "latin-1"


def ler_zip(caminho: Path, ao_ler_fracao):
    """Gera as linhas (listas de campos) do CSV dentro do zip, em streaming."""
    with zipfile.ZipFile(caminho) as zf:
        membros = [m for m in zf.infolist() if not m.is_dir()]
        if not membros:
            raise RuntimeError(f"{caminho.name} está vazio")
        membro = max(membros, key=lambda m: m.file_size)
        total = membro.file_size or 1
        with zf.open(membro) as bruto:
            cod = detectar_codificacao(bruto.read(1 << 18))
        cru = LeitorContado(zf.open(membro), lambda n: ao_ler_fracao(n / total))
        texto = io.TextIOWrapper(io.BufferedReader(cru, 1 << 20), encoding=cod,
                                 errors="replace", newline="")
        linhas = (ln.replace("\x00", "") if "\x00" in ln else ln for ln in texto)
        try:
            yield from csv.reader(linhas, delimiter=";", quotechar='"')
        finally:
            texto.close()


def arquivos_presentes(pasta: Path, nomes: list[str]) -> list[Path]:
    """Aceita os zips com o nome oficial em qualquer caixa (ex.: ESTABELECIMENTOS0.zip)."""
    existentes = {p.name.lower(): p for p in pasta.iterdir() if p.is_file()}
    faltando = [n for n in nomes if n.lower() not in existentes]
    if faltando:
        raise RuntimeError(f"faltam arquivos em {pasta}: {', '.join(faltando)}")
    return [existentes[n.lower()] for n in nomes]


# --------------------------------------------------------------------------
# Importação
# --------------------------------------------------------------------------
LOTE = 20000


def importar(pasta: Path, ufs: set[str], situacoes: set[int], mes: str, fonte: str,
             prog: Progresso) -> int:
    for p in (BANCO_NOVO, MARCA_NOVO, STAGING):
        p.unlink(missing_ok=True)
    stg = sqlite3.connect(STAGING)
    stg.executescript("""
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=MEMORY;
        CREATE TABLE estab (
            cnpj TEXT PRIMARY KEY, cnpj_basico TEXT, nome_fantasia TEXT, situacao INTEGER,
            data_situacao TEXT, data_inicio TEXT, cnae_principal TEXT, cnaes_secundarios TEXT,
            logradouro TEXT, numero TEXT, complemento TEXT, bairro TEXT, cep TEXT, uf TEXT,
            municipio_codigo TEXT, telefone1 TEXT, telefone2 TEXT, email TEXT, celular INTEGER,
            fantasia_busca TEXT, bairro_busca TEXT);
        CREATE TABLE simples (
            cnpj_basico TEXT PRIMARY KEY, tipo TEXT, opcao_simples TEXT, data_opcao_mei TEXT,
            data_exclusao_mei TEXT, data_exclusao_simples TEXT);
        CREATE TABLE emp (
            cnpj_basico TEXT PRIMARY KEY, razao_social TEXT, razao_busca TEXT,
            natureza_juridica TEXT, capital_social REAL, porte TEXT);
        CREATE TABLE cnaes (codigo TEXT PRIMARY KEY, descricao TEXT);
        CREATE TABLE municipios (codigo TEXT PRIMARY KEY, nome TEXT);
    """)

    caminhos = dict(zip(ARQUIVOS, arquivos_presentes(pasta, ARQUIVOS)))

    # Tabelas auxiliares --------------------------------------------------
    prog.fase("auxiliares", "Lendo tabelas de atividades e municípios")
    cnaes = [(r[0].strip().zfill(7), limpar(r[1])) for r in ler_zip(caminhos["Cnaes.zip"], lambda f: None)
             if len(r) >= 2 and r[0].strip().isdigit()]
    stg.executemany("INSERT OR REPLACE INTO cnaes VALUES (?,?)", cnaes)
    muns = [(r[0].strip().zfill(4), limpar(r[1])) for r in ler_zip(caminhos["Municipios.zip"], lambda f: None)
            if len(r) >= 2 and r[0].strip()]
    stg.executemany("INSERT OR REPLACE INTO municipios VALUES (?,?)", muns)
    prog.log(f"{len(cnaes)} atividades (CNAE) e {len(muns)} municípios carregados.")

    # Estabelecimentos ----------------------------------------------------
    prog.fase("estabelecimentos", "Separando estabelecimentos de " + ", ".join(sorted(ufs)))
    basicos: set[str] = set()
    lidos = guardados = ruins = 0
    for n, arq in enumerate(ARQ_ESTAB):
        lote = []
        for row in ler_zip(caminhos[arq], lambda f, n=n, arq=arq: prog.avancar(
                (n + f) / len(ARQ_ESTAB), f"{arq}: {f * 100:.0f}% lido, {guardados:,} guardados".replace(",", "."))):
            lidos += 1
            if len(row) < 28:
                ruins += 1
                continue
            if row[19].strip() not in ufs:
                continue
            sit = inteiro(row[5])
            if sit not in situacoes:
                continue
            tel1, tel2 = telefone(row[21], row[22]), telefone(row[23], row[24])
            tipo_log, log_ = limpar(row[13]), limpar(row[14])
            logradouro = f"{tipo_log} {log_}".strip() if not log_.upper().startswith(tipo_log.upper() + " ") else log_
            fantasia, bairro = limpar(row[4]), limpar(row[17])
            lote.append((
                (row[0] + row[1] + row[2]).strip().upper(), row[0].strip().upper(), fantasia, sit,
                data_iso(row[6]), data_iso(row[10]), row[11].strip().zfill(7) if row[11].strip() else None,
                ",".join(c.strip().zfill(7) for c in row[12].split(",") if c.strip()) or None,
                logradouro or None, limpar(row[15]) or None, limpar(row[16]) or None, bairro or None,
                re.sub(r"\D", "", row[18]) or None, row[19].strip(), row[20].strip().zfill(4),
                tel1, tel2, (row[27].strip().lower() or None) if len(row) > 27 else None,
                1 if (eh_celular(tel1) or eh_celular(tel2)) else 0,
                normalizar(fantasia), normalizar(bairro),
            ))
            basicos.add(row[0].strip().upper())
            if len(lote) >= LOTE:
                stg.executemany("INSERT OR REPLACE INTO estab VALUES (" + ",".join("?" * 21) + ")", lote)
                guardados += len(lote)
                lote.clear()
        stg.executemany("INSERT OR REPLACE INTO estab VALUES (" + ",".join("?" * 21) + ")", lote)
        guardados += len(lote)
        stg.commit()
        prog.log(f"{arq}: concluído ({guardados:,} estabelecimentos no(s) estado(s) até agora).".replace(",", "."))
    if ruins:
        prog.log(f"Aviso: {ruins} linhas mal formadas foram ignoradas em {lidos:,} lidas.".replace(",", "."))
    if not guardados:
        raise RuntimeError("nenhum estabelecimento encontrado para " + ", ".join(sorted(ufs)))

    # Simples / MEI -------------------------------------------------------
    prog.fase("simples", "Identificando quem é ou já foi MEI")
    meis: set[str] = set()
    lote = []
    n_mei = n_ex = 0
    for row in ler_zip(caminhos["Simples.zip"], lambda f: prog.avancar(f, f"Simples.zip: {f * 100:.0f}% lido")):
        if len(row) < 7:
            continue
        basico = row[0].strip().upper()
        if basico not in basicos:
            continue
        opcao_mei = row[4].strip().upper()
        d_opc, d_exc = data_iso(row[5]), data_iso(row[6])
        if opcao_mei == "S":
            tipo = "MEI"
            n_mei += 1
        elif d_exc or d_opc:
            tipo = "EX-MEI"
            n_ex += 1
        else:
            continue
        lote.append((basico, tipo, row[1].strip().upper() or None, d_opc, d_exc, data_iso(row[3])))
        meis.add(basico)
        if len(lote) >= LOTE:
            stg.executemany("INSERT OR REPLACE INTO simples VALUES (?,?,?,?,?,?)", lote)
            lote.clear()
    stg.executemany("INSERT OR REPLACE INTO simples VALUES (?,?,?,?,?,?)", lote)
    stg.commit()
    del basicos
    prog.log(f"{n_mei:,} MEIs e {n_ex:,} ex-MEIs encontrados.".replace(",", "."))

    # Empresas (razão social) --------------------------------------------
    prog.fase("empresas", "Buscando razão social")
    achadas = 0
    for n, arq in enumerate(ARQ_EMPRESAS):
        lote = []
        for row in ler_zip(caminhos[arq], lambda f, n=n, arq=arq: prog.avancar(
                (n + f) / len(ARQ_EMPRESAS), f"{arq}: {f * 100:.0f}% lido")):
            if len(row) < 6:
                continue
            basico = row[0].strip().upper()
            if basico not in meis:
                continue
            razao = tirar_cpf(limpar(row[1]))
            lote.append((basico, razao, normalizar(razao), row[2].strip() or None,
                         numero_br(row[4]), row[5].strip() or None))
            if len(lote) >= LOTE:
                stg.executemany("INSERT OR REPLACE INTO emp VALUES (?,?,?,?,?,?)", lote)
                achadas += len(lote)
                lote.clear()
        stg.executemany("INSERT OR REPLACE INTO emp VALUES (?,?,?,?,?,?)", lote)
        achadas += len(lote)
        stg.commit()
    prog.log(f"Razão social encontrada para {achadas:,} empresas.".replace(",", "."))
    stg.close()

    # Banco final ----------------------------------------------------------
    prog.fase("final", "Montando o banco final")
    db = sqlite3.connect(BANCO_NOVO)
    db.execute("ATTACH DATABASE ? AS stg", (str(STAGING),))
    db.executescript("""
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=MEMORY;
        CREATE TABLE empresas (
            cnpj TEXT PRIMARY KEY, razao_social TEXT, nome_fantasia TEXT, tipo TEXT,
            situacao INTEGER, data_situacao TEXT, data_inicio TEXT,
            cnae_principal TEXT, cnae_descricao TEXT, cnaes_secundarios TEXT,
            logradouro TEXT, numero TEXT, complemento TEXT, bairro TEXT, cep TEXT,
            municipio_codigo TEXT, municipio TEXT, uf TEXT,
            telefone1 TEXT, telefone2 TEXT, email TEXT, celular INTEGER,
            data_opcao_mei TEXT, data_exclusao_mei TEXT, opcao_simples TEXT, data_exclusao_simples TEXT,
            natureza_juridica TEXT, capital_social REAL, porte TEXT,
            busca TEXT, bairro_busca TEXT);
        INSERT INTO empresas
        SELECT e.cnpj, COALESCE(NULLIF(p.razao_social,''), e.nome_fantasia, ''), e.nome_fantasia, s.tipo,
               e.situacao, e.data_situacao, e.data_inicio,
               e.cnae_principal, c.descricao, e.cnaes_secundarios,
               e.logradouro, e.numero, e.complemento, e.bairro, e.cep,
               e.municipio_codigo, COALESCE(m.nome, e.municipio_codigo), e.uf,
               e.telefone1, e.telefone2, e.email, e.celular,
               s.data_opcao_mei, s.data_exclusao_mei, s.opcao_simples, s.data_exclusao_simples,
               p.natureza_juridica, p.capital_social, p.porte,
               TRIM(COALESCE(p.razao_busca,'') || ' ' || COALESCE(e.fantasia_busca,'')), e.bairro_busca
        FROM stg.estab e
        JOIN stg.simples s ON s.cnpj_basico = e.cnpj_basico
        LEFT JOIN stg.emp p ON p.cnpj_basico = e.cnpj_basico
        LEFT JOIN stg.cnaes c ON c.codigo = e.cnae_principal
        LEFT JOIN stg.municipios m ON m.codigo = e.municipio_codigo;
        CREATE TABLE cnaes AS SELECT * FROM stg.cnaes;
        CREATE UNIQUE INDEX idx_cnaes ON cnaes(codigo);
        CREATE TABLE municipios AS SELECT * FROM stg.municipios;
        CREATE TABLE cnae_contagem AS
            SELECT e.cnae_principal AS codigo, COALESCE(c.descricao, e.cnae_principal) AS descricao,
                   '' AS descricao_busca, COUNT(*) AS total
            FROM empresas e LEFT JOIN cnaes c ON c.codigo = e.cnae_principal
            WHERE e.cnae_principal IS NOT NULL GROUP BY e.cnae_principal;
        DETACH DATABASE stg;
    """)
    prog.avancar(0.5, "Criando índices")
    db.create_function("normalizar", 1, normalizar, deterministic=True)
    db.executescript("""
        UPDATE cnae_contagem SET descricao_busca = codigo || ' ' || normalizar(descricao);
        CREATE INDEX idx_mun ON empresas(municipio_codigo, bairro_busca);
        CREATE INDEX idx_mun_inicio ON empresas(municipio_codigo, data_inicio);
        CREATE INDEX idx_uf ON empresas(uf);
        CREATE INDEX idx_cnae ON empresas(cnae_principal);
        CREATE INDEX idx_inicio ON empresas(data_inicio);
        CREATE INDEX idx_exclusao ON empresas(data_exclusao_mei);
        CREATE INDEX idx_tipo ON empresas(tipo, situacao);
        CREATE TABLE info (chave TEXT PRIMARY KEY, valor TEXT);
        ANALYZE;
    """)
    total = db.execute("SELECT COUNT(*) FROM empresas").fetchone()[0]
    mais_recente = db.execute("SELECT MAX(data_inicio) FROM empresas").fetchone()[0]
    db.executemany("INSERT INTO info VALUES (?,?)", [
        ("mes_referencia", mes), ("ufs", ",".join(sorted(ufs))), ("importado_em", agora()),
        ("total", str(total)), ("data_mais_recente", mais_recente or ""), ("fonte", fonte),
        ("situacoes", ",".join(str(s) for s in sorted(situacoes))),
    ])
    db.commit()
    db.close()
    STAGING.unlink(missing_ok=True)
    MARCA_NOVO.write_text(agora(), encoding="utf-8")
    return total


def instalar_base_nova(prog: Progresso) -> bool:
    """Troca o banco antigo pelo novo. Se o sistema estiver aberto no Windows e a
    troca falhar, o próprio sistema faz a troca na próxima consulta."""
    for _ in range(5):
        try:
            os.replace(BANCO_NOVO, BANCO)
            MARCA_NOVO.unlink(missing_ok=True)
            return True
        except PermissionError:
            time.sleep(2)
    prog.log("A base nova está pronta e será ativada automaticamente pelo sistema.")
    return False


def ler_info_atual() -> dict:
    if not BANCO.exists():
        return {}
    try:
        with sqlite3.connect(f"file:{BANCO.as_posix()}?mode=ro", uri=True) as db:
            return dict(db.execute("SELECT chave, valor FROM info").fetchall())
    except sqlite3.Error:
        return {}


def outra_atualizacao_rodando() -> bool:
    if not TRAVA.exists():
        return False
    try:
        estado = json.loads(PROGRESSO.read_text(encoding="utf-8"))
        ultima = datetime.fromisoformat(estado.get("atualizado_em", "2000-01-01"))
        return estado.get("status") == "executando" and (datetime.now() - ultima).total_seconds() < 900
    except (OSError, ValueError):
        return False


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Atualiza a base local de MEIs a partir da Receita Federal.")
    ap.add_argument("--uf", default=os.environ.get("PROSPECTA_UF", "CE"),
                    help="Estado(s) separados por vírgula. Padrão: CE")
    ap.add_argument("--mes", help="Mês da base no formato AAAA-MM. Padrão: o mais recente")
    ap.add_argument("--fonte", choices=["auto", "receita", "espelho"], default="auto",
                    help="De onde baixar. 'auto' tenta a Receita e usa o espelho se ela falhar")
    ap.add_argument("--pasta-zips", default=str(PASTA_ZIPS), help="Onde guardar os zips baixados")
    ap.add_argument("--somente-importar", action="store_true",
                    help="Não baixa nada; importa os zips que já estão em --pasta-zips")
    ap.add_argument("--incluir-baixadas", action="store_true",
                    help="Guarda também empresas baixadas/nulas (deixa a base bem maior)")
    ap.add_argument("--forcar", action="store_true", help="Reimporta mesmo se a base já for do mês mais recente")
    ap.add_argument("--apagar-zips", action="store_true", help="Apaga os zips depois de importar (~7 GB)")
    args = ap.parse_args()

    ufs = {u.strip().upper() for u in args.uf.split(",") if u.strip()}
    invalidas = ufs - UFS_VALIDAS
    if not ufs or invalidas:
        print(f"UF inválida: {', '.join(invalidas) or '(vazio)'}")
        return 2
    situacoes = SITUACOES_PADRAO | ({1, 8} if args.incluir_baixadas else set())

    DADOS_DIR.mkdir(parents=True, exist_ok=True)
    if outra_atualizacao_rodando():
        print("Já existe uma atualização em andamento. Aguarde ela terminar.")
        return 3
    TRAVA.write_text(str(os.getpid()), encoding="utf-8")

    faixas = ({"auxiliares": (0, 2), "estabelecimentos": (2, 60), "simples": (60, 70),
               "empresas": (70, 92), "final": (92, 100)} if args.somente_importar else
              {"download": (0, 55), "auxiliares": (55, 56), "estabelecimentos": (56, 80),
               "simples": (80, 85), "empresas": (85, 96), "final": (96, 100)})
    prog = Progresso(faixas)
    inicio = time.time()
    try:
        info = ler_info_atual()
        if args.somente_importar:
            pasta = Path(args.pasta_zips)
            if not pasta.is_dir():
                raise RuntimeError(f"pasta não encontrada: {pasta}")
            mes = args.mes or (pasta.name if re.fullmatch(r"\d{4}-\d{2}", pasta.name) else datetime.now().strftime("%Y-%m"))
            fonte = "demonstração" if pasta.name == "demo_zips" else "arquivos locais"
        else:
            if info.get("ufs") and info["ufs"] != ",".join(sorted(ufs)):
                args.forcar = True  # mudou o estado: precisa reimportar
            if info.get("fonte") == "demonstração":
                args.forcar = True  # troca os dados fictícios pela base real
            escolha = escolher_mes_e_baixar(args, prog, info.get("mes_referencia"))
            if escolha is None:
                prog.concluir("atualizado", f"A base já é a mais recente ({info.get('mes_referencia')}). Nada a fazer.")
                return 0
            mes, pasta, fonte = escolha

        prog.log(f"Importando {', '.join(sorted(ufs))} a partir de {pasta}.")
        total = importar(pasta, ufs, situacoes, mes, fonte, prog)
        instalar_base_nova(prog)

        if not args.somente_importar:
            raiz = Path(args.pasta_zips)
            for antiga in raiz.iterdir():
                if antiga.is_dir() and antiga.name != mes and re.fullmatch(r"\d{4}-\d{2}", antiga.name):
                    shutil.rmtree(antiga, ignore_errors=True)
            if args.apagar_zips:
                shutil.rmtree(pasta, ignore_errors=True)
        dur = (time.time() - inicio) / 60
        prog.concluir("concluido", f"Pronto: {total:,} empresas na base de {mes} ({dur:.0f} min).".replace(",", "."))
        return 0
    except KeyboardInterrupt:
        prog.concluir("erro", "Atualização interrompida. Rode de novo para continuar de onde parou.")
        return 130
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        prog.concluir("erro", f"Erro: {e}")
        return 1
    finally:
        for p in (TRAVA, STAGING):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
