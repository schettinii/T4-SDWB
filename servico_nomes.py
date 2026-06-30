import socket
import threading
import json
import rede

# o servico de nomes e o unico que tem endereco fixo - todo mundo conhece ele.
IP = ""        # escuta em todas as interfaces da maquina
PORTA = 6000

# as "paginas amarelas": nome do quadro -> (ip, porta) do coordenador daquele quadro
quadros = {}
trava = threading.Lock()


def trata_cliente(conexao):
    linha = rede.le_linha(conexao)
    if linha is None:
        conexao.close()
        return
    msg = json.loads(linha)
    op = msg["op"]

    if op == "registrar":
        # TODO: e se ja existir um quadro com esse nome? por enquanto so sobrescreve
        with trava:
            quadros[msg["nome"]] = (msg["ip"], msg["porta"])
        rede.responde(conexao, {"ok": True})
        print("registrou quadro:", msg["nome"], "->", msg["ip"], msg["porta"])

    elif op == "listar":
        with trava:
            lista = []
            for nome in quadros:
                ip, porta = quadros[nome]
                lista.append([nome, ip, porta])
        rede.responde(conexao, {"quadros": lista})

    elif op == "remover":
        with trava:
            if msg["nome"] in quadros:
                del quadros[msg["nome"]]
        rede.responde(conexao, {"ok": True})
        print("removeu quadro:", msg["nome"])

    conexao.close()


def dump_tabela():
    # debug: ver tudo que ta registrado nas paginas amarelas
    for nome in quadros:
        print(nome, "->", quadros[nome])


servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
servidor.bind((IP, PORTA))
servidor.listen()
print("Servico de nomes rodando na porta", PORTA)

while True:
    conexao, endereco = servidor.accept()
    t = threading.Thread(target=trata_cliente, args=(conexao,))
    t.start()
