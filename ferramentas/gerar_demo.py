#!/usr/bin/env python3
"""
Gera zips FICTÍCIOS no mesmo layout dos Dados Abertos do CNPJ, para testar o
sistema sem baixar ~7 GB. Nomes, telefones e e-mails são inventados.

    python ferramentas/gerar_demo.py              # cria dados/demo_zips/
    python atualizar_base.py --somente-importar --pasta-zips dados/demo_zips --mes 2026-10
"""
import csv
import io
import random
import string
import sys
import unicodedata
import zipfile
from datetime import date, timedelta
from pathlib import Path

DESTINO = Path(__file__).resolve().parent.parent / "dados" / "demo_zips"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 30000
random.seed(42)

CNAES = {
    "4781400": "Comércio varejista de artigos do vestuário e acessórios",
    "9602501": "Cabeleireiros, manicure e pedicure",
    "5611203": "Lanchonetes, casas de chá, de sucos e similares",
    "4712100": "Comércio varejista de mercadorias em geral, com predominância de produtos alimentícios - minimercados, mercearias e armazéns",
    "4399103": "Obras de alvenaria",
    "7319002": "Promoção de vendas",
    "4520001": "Serviços de manutenção e reparação mecânica de veículos automotores",
    "9602502": "Atividades de estética e outros serviços de cuidados com a beleza",
    "5620104": "Fornecimento de alimentos preparados preponderantemente para consumo domiciliar",
    "4321500": "Instalação e manutenção elétrica",
    "8219999": "Preparação de documentos e serviços especializados de apoio administrativo não especificados anteriormente",
    "4930201": "Transporte rodoviário de carga, exceto produtos perigosos e mudanças, municipal",
    "1412601": "Confecção de peças do vestuário, exceto roupas íntimas e as confeccionadas sob medida",
    "9511800": "Reparação e manutenção de computadores e de equipamentos periféricos",
    "4723700": "Comércio varejista de bebidas",
    "7420001": "Atividades de produção de fotografias, exceto aérea e submarina",
    "4772500": "Comércio varejista de cosméticos, produtos de perfumaria e de higiene pessoal",
    "8599699": "Outras atividades de ensino não especificadas anteriormente",
}
PESOS_CNAE = [14, 12, 10, 9, 6, 6, 5, 5, 5, 4, 4, 3, 3, 3, 3, 2, 3, 2]

MUNICIPIOS = {  # códigos fictícios para a demonstração
    "1389": ("FORTALEZA", "CE", 40), "1373": ("CAUCAIA", "CE", 8), "1585": ("MARACANAU", "CE", 6),
    "1495": ("JUAZEIRO DO NORTE", "CE", 6), "1559": ("SOBRAL", "CE", 5), "1383": ("CRATO", "CE", 3),
    "1453": ("ITAPIPOCA", "CE", 3), "1489": ("IGUATU", "CE", 2), "1561": ("QUIXADA", "CE", 2),
    "1311": ("AQUIRAZ", "CE", 2), "1576": ("EUSEBIO", "CE", 2), "1059": ("TERESINA", "PI", 6),
    "1761": ("NATAL", "RN", 5),
}
BAIRROS_FORTALEZA = ["ALDEOTA", "MEIRELES", "MESSEJANA", "MONTESE", "PARANGABA", "BENFICA", "CENTRO",
                     "JANGURUSSU", "BARRA DO CEARÁ", "COCÓ", "PAPICU", "BOM JARDIM", "MONDUBIM",
                     "GRANJA PORTUGAL", "CONJUNTO CEARÁ", "HENRIQUE JORGE", "ANTÔNIO BEZERRA", "PICI",
                     "SERRINHA", "CIDADE DOS FUNCIONÁRIOS", "PASSARÉ", "MARAPONGA", "JOAQUIM TÁVORA",
                     "FÁTIMA", "VICENTE PINZÓN"]
BAIRROS_OUTROS = ["CENTRO", "PLANALTO", "JARDIM", "SÃO JOSÉ", "NOVA ESPERANÇA", "ALTO ALEGRE"]
NOMES = ["MARIA", "JOSÉ", "ANTÔNIA", "FRANCISCO", "FRANCISCA", "JOÃO", "ANA", "RAIMUNDO", "LUCIANA",
         "PEDRO", "JULIANA", "CÍCERO", "PATRÍCIA", "PAULO", "FERNANDA", "RAFAEL", "GABRIELA", "ÍTALO"]
SOBRENOMES = ["SILVA", "SOUSA", "OLIVEIRA", "LIMA", "PEREIRA", "COSTA", "RODRIGUES", "ALVES",
              "NASCIMENTO", "FERREIRA", "CAVALCANTE", "BEZERRA", "MOREIRA", "HOLANDA", "FEITOSA"]
FANTASIAS = ["{} MODAS", "SALÃO DA {}", "LANCHES DO {}", "MERCADINHO {}", "{} CONSTRUÇÕES",
             "BELEZA {}", "AUTO CENTER {}", "{} ELÉTRICA", "", "", "", "DOCES DA {}", "{} FOTOGRAFIA"]
LOGRADOUROS = ["RUA", "AVENIDA", "TRAVESSA", "RUA", "RUA"]
RUAS = ["DOS TABAJARAS", "BARÃO DE STUDART", "JOSÉ BASTOS", "DOM LUÍS", "CASTRO E SILVA",
        "BEZERRA DE MENEZES", "TREZE DE MAIO", "PADRE CÍCERO", "SANTOS DUMONT", "DUQUE DE CAXIAS"]

HOJE = date(2026, 10, 11)


def d(dt):
    return dt.strftime("%Y%m%d") if dt else ""


def data_aleatoria(dias_max, peso_recente=False):
    x = random.random() ** (2.5 if peso_recente else 1)
    return HOJE - timedelta(days=int(x * dias_max))


def basico_alfanumerico():
    return "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(8))


def escrever_zip(nome_zip, nome_csv, linhas, codificacao="latin-1"):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quotechar='"', quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerows(linhas)
    with zipfile.ZipFile(DESTINO / nome_zip, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(nome_csv, buf.getvalue().encode(codificacao, errors="replace"))


def main():
    DESTINO.mkdir(parents=True, exist_ok=True)
    escrever_zip("Cnaes.zip", "F.K03200$Z.D61011.CNAECSV", [[k, v] for k, v in CNAES.items()])
    escrever_zip("Municipios.zip", "F.K03200$Z.D61011.MUNICCSV", [[k, v[0]] for k, v in MUNICIPIOS.items()])

    cods_mun = list(MUNICIPIOS)
    pesos_mun = [v[2] for v in MUNICIPIOS.values()]
    estab, empresas, simples = [[] for _ in range(10)], [[] for _ in range(10)], []
    usados = set()
    for i in range(N):
        basico = basico_alfanumerico() if random.random() < 0.02 else f"{random.randint(10_000_000, 66_999_999):08d}"
        if basico in usados:
            continue
        usados.add(basico)
        cod = random.choices(cods_mun, pesos_mun)[0]
        mun, uf, _ = MUNICIPIOS[cod]
        perfil = random.random()
        situacao = random.choices(["02", "04", "08", "03"], [78, 9, 12, 1])[0]
        inicio = data_aleatoria(3650, peso_recente=True)
        nome = f"{random.choice(NOMES)} {random.choice(SOBRENOMES)} {random.choice(SOBRENOMES)}"
        cpf = f"{random.randint(0, 99999999999):011d}"
        fant = random.choice(FANTASIAS)
        fant = fant.format(random.choice(SOBRENOMES)) if fant else ""
        cnae = random.choices(list(CNAES), PESOS_CNAE)[0]
        secs = ",".join(random.sample(list(CNAES), random.randint(0, 3)))
        bairro = random.choice(BAIRROS_FORTALEZA if cod == "1389" else BAIRROS_OUTROS)
        celular = random.random() < 0.7
        fone = f"9{random.randint(8000_0000, 9999_9999)}" if celular else f"3{random.randint(100_0000, 999_9999)}"
        if random.random() < 0.15:
            fone, celular = "", False
        primeiro = unicodedata.normalize("NFKD", nome.split()[0]).encode("ascii", "ignore").decode().lower()
        email = f"{primeiro}.{random.randint(1, 999)}@exemplo.com" if random.random() < 0.55 else ""
        if i == 7:  # registro com quebra de linha e aspas dentro de campo
            fant = 'BAR "DO ZÉ"\nFILIAL'
        k = i % 10
        estab[k].append([
            basico[:8], "0001", f"{random.randint(0, 99):02d}", "1", fant, situacao,
            d(inicio + timedelta(days=random.randint(0, 400))) if situacao != "02" else d(inicio), "00", "", "",
            d(inicio), cnae, secs, random.choice(LOGRADOUROS), random.choice(RUAS),
            str(random.randint(1, 3000)) if random.random() < 0.9 else "S/N",
            random.choice(["", "", "LOJA 2", "CASA B", "APTO 101"]), bairro,
            f"60{random.randint(100000, 999999)}", uf, cod,
            "85" if uf == "CE" else "86", fone, "", "", "", "", email.upper() if random.random() < 0.3 else email, "", "",
        ])
        if perfil < 0.72:
            simples.append([basico, "S", d(inicio), "00000000", "S", d(inicio), "00000000"])
        elif perfil < 0.86:
            excl = data_aleatoria(1100, peso_recente=True)
            excl = min(HOJE, max(excl, inicio + timedelta(days=30)))
            simples.append([basico, "S", d(inicio), "", "N", d(inicio), d(excl)])
        elif perfil < 0.95:
            simples.append([basico, "S", d(inicio), "00000000", "N", "00000000", "00000000"])
        nat = "2135" if perfil < 0.86 else random.choice(["2062", "2135", "2305"])
        empresas[k].append([basico, f"{nome} {cpf}" if nat == "2135" else f"{fant or nome} LTDA",
                            nat, "50", f"{random.choice([1000, 5000, 10000, 50000])},00",
                            random.choice(["01", "01", "03"]), ""])

    # linha mal formada (deve ser ignorada sem derrubar a importação)
    estab[0].append(["123", "QUEBRADA"])
    for k in range(10):
        escrever_zip(f"Estabelecimentos{k}.zip", f"K3241.K03200Y{k}.D61011.ESTABELE", estab[k])
        escrever_zip(f"Empresas{k}.zip", f"K3241.K03200Y{k}.D61011.EMPRECSV", empresas[k])
    escrever_zip("Simples.zip", "F.K03200$W.SIMPLES.CSV.D61011", simples)
    print(f"Dados de demonstração gerados em {DESTINO} ({len(usados)} empresas).")


if __name__ == "__main__":
    main()
