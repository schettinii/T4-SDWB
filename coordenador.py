import socket
import threading
import json
import time
import rede

T = 3   # intervalo do heartbeat, em segundos


# O coordenador e o "dono" de um quadro: guarda os objetos desenhados, a lista de
# clientes participando e repassa toda acao para todo mundo. Ele roda dentro do
# processo de um cliente (o que criou o quadro, ou o que venceu a eleicao).
class Coordenador:
    def __init__(self, nome, ip, porta, objetos=None, participantes=None):
        self.nome = nome
        self.ip = ip
        self.porta = porta
        self.objetos = objetos if objetos else []
        self.participantes = participantes if participantes else []
        self.selecoes = {}        # id do objeto -> [ip, porta] de quem selecionou
        self.proximo_id = 1
        # se ja veio com objetos (coordenador novo assumindo apos eleicao),
        # continua a contagem de id de onde estava pra nao repetir
        for obj in self.objetos:
            if obj["id"] >= self.proximo_id:
                self.proximo_id = obj["id"] + 1
        self.ativo = False
        self.trava = threading.Lock()

    def iniciar(self):
        self.servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.servidor.bind((self.ip, self.porta))
        self.porta = self.servidor.getsockname()[1]   # se a porta foi 0, pega a que o SO deu
        self.servidor.listen()
        self.ativo = True
        threading.Thread(target=self._laco_accept, daemon=True).start()
        threading.Thread(target=self._laco_heartbeat, daemon=True).start()

    def parar(self):
        self.ativo = False
        try:
            self.servidor.close()
        except:
            pass

    def _laco_accept(self):
        while self.ativo:
            try:
                conexao, endereco = self.servidor.accept()
            except:
                break
            threading.Thread(target=self._trata, args=(conexao,), daemon=True).start()

    def _trata(self, conexao):
        linha = rede.le_linha(conexao)
        if linha is None:
            conexao.close()
            return
        msg = json.loads(linha)
        op = msg["op"]

        if op == "ingressar":
            cliente = [msg["ip"], msg["porta"]]
            with self.trava:
                if cliente not in self.participantes:
                    self.participantes.append(cliente)
                estado = {"objetos": self.objetos, "participantes": self.participantes}
            rede.responde(conexao, estado)
            self._broadcast({"op": "participantes", "lista": self.participantes})

        elif op == "sair":
            cliente = [msg["ip"], msg["porta"]]
            with self.trava:
                if cliente in self.participantes:
                    self.participantes.remove(cliente)
                self._libera_do_cliente(cliente)
            rede.responde(conexao, {"ok": True})
            self._broadcast({"op": "participantes", "lista": self.participantes})

        elif op == "add_linha" or op == "add_quadrado":
            tipo = "linha" if op == "add_linha" else "quadrado"
            with self.trava:
                obj = {"id": self.proximo_id, "tipo": tipo,
                       "x1": msg["x1"], "y1": msg["y1"],
                       "x2": msg["x2"], "y2": msg["y2"], "cor": "black"}
                self.proximo_id += 1
                self.objetos.append(obj)
            rede.responde(conexao, {"ok": True})
            self._broadcast({"op": "novo_objeto", "objeto": obj})

        elif op == "selecionar":
            cliente = [msg["ip"], msg["porta"]]
            oid = msg["obj_id"]
            with self.trava:
                dono = self.selecoes.get(oid)
                if dono is not None and dono != cliente:
                    rede.responde(conexao, {"ok": False, "erro": "objeto ja selecionado por outro usuario"})
                else:
                    # cada cliente fica com no maximo um objeto selecionado por vez
                    self._libera_do_cliente(cliente)
                    self.selecoes[oid] = cliente
                    rede.responde(conexao, {"ok": True})

        elif op == "liberar":
            cliente = [msg["ip"], msg["porta"]]
            with self.trava:
                self._libera_do_cliente(cliente)
            rede.responde(conexao, {"ok": True})

        elif op == "colorir":
            cliente = [msg["ip"], msg["porta"]]
            oid = msg["obj_id"]
            with self.trava:
                dono = self.selecoes.get(oid)
                if dono != cliente:
                    rede.responde(conexao, {"ok": False, "erro": "selecione o objeto antes"})
                else:
                    for obj in self.objetos:
                        if obj["id"] == oid:
                            obj["cor"] = msg["cor"]
                    del self.selecoes[oid]
                    rede.responde(conexao, {"ok": True})
                    self._broadcast({"op": "obj_colorido", "obj_id": oid, "cor": msg["cor"]})

        elif op == "remover":
            cliente = [msg["ip"], msg["porta"]]
            oid = msg["obj_id"]
            with self.trava:
                dono = self.selecoes.get(oid)
                if dono != cliente:
                    rede.responde(conexao, {"ok": False, "erro": "selecione o objeto antes"})
                else:
                    self.objetos = [o for o in self.objetos if o["id"] != oid]
                    del self.selecoes[oid]
                    rede.responde(conexao, {"ok": True})
                    self._broadcast({"op": "obj_removido", "obj_id": oid})

        elif op == "ping":
            rede.responde(conexao, {"ok": True})

        conexao.close()

    # tira todas as selecoes desse cliente. quem chama ja deve estar com a trava.
    def _libera_do_cliente(self, cliente):
        for oid in list(self.selecoes.keys()):
            if self.selecoes[oid] == cliente:
                del self.selecoes[oid]

    # manda a mesma mensagem pra todos os participantes
    def _broadcast(self, msg):
        for p in list(self.participantes):
            try:
                rede.envia(p[0], p[1], msg)
            except:
                pass   # cliente caiu; o heartbeat tira ele da lista depois

    def _laco_heartbeat(self):
        falhas = {}
        while self.ativo:
            time.sleep(T)
            for p in list(self.participantes):
                chave = tuple(p)
                try:
                    rede.envia(p[0], p[1], {"op": "ping"})
                    falhas[chave] = 0
                except:
                    falhas[chave] = falhas.get(chave, 0) + 1
            # quem nao respondeu duas vezes seguidas (2T sem dar sinal) sai do quadro
            mortos = [p for p in list(self.participantes) if falhas.get(tuple(p), 0) >= 2]
            if mortos:
                with self.trava:
                    for p in mortos:
                        if p in self.participantes:
                            self.participantes.remove(p)
                        self._libera_do_cliente(p)
                        falhas[tuple(p)] = 0
                self._broadcast({"op": "participantes", "lista": self.participantes})
                print("tirei do quadro:", mortos)
