import socket
import json


# manda uma mensagem (dict) para ip:porta e devolve a resposta, ou None se nao vier nada.
# se nao conseguir conectar estoura excecao - quem chama trata quando precisa.
def envia(ip, porta, msg):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    s.connect((ip, porta))
    s.sendall((json.dumps(msg) + "\n").encode())
    resposta = le_linha(s)
    s.close()
    if resposta is None:
        return None
    return json.loads(resposta)


# le bytes do socket ate achar o \n que marca o fim da mensagem
def le_linha(sock):
    dados = b""
    while b"\n" not in dados:
        parte = sock.recv(1024)
        if not parte:
            break
        dados += parte
    if not dados:
        return None
    linha = dados.split(b"\n")[0]
    return linha.decode()


# responde uma mensagem com a conexao ja aberta (usado pelos servidores)
def responde(conexao, msg):
    conexao.sendall((json.dumps(msg) + "\n").encode())


# descobre o ip da maquina na rede: abre um socket pra fora e ve com que ip o SO saiu.
# nao manda nada de verdade, so serve pra pegar o endereco.
def meu_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except:
        ip = "127.0.0.1"
    s.close()
    return ip
