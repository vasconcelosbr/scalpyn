# Liberação de edições durante recuperação

A API deixa de bloquear cadastros, conexões de exchange e configurações apenas por estar em modo de observação. Autenticação, validação, escopo por usuário e criptografia continuam aplicados pelos endpoints.

`RECOVERY_OBSERVATION_ONLY` continua controlando a proteção de escrita na Gate e a execução observacional dos workers. O bloqueio legado de edição exige agora `RECOVERY_BLOCK_APP_EDITS`, ausente por padrão. O mesmo controle separado é usado pelo salvamento agregado de estratégias.

Validação: testes de gravação autenticada de conexão com criptografia, gravação e releitura de estratégia/Spot com invalidação de cache, rotas legadas e bloqueio de ordens antes da rede. A suíte ampliada encontrou uma falha preexistente em `test_l3_policy_materialization_is_scoped_idempotent_and_read_back`, reproduzida também com o serviço original de `origin/main`: `L3 gate policy readback hash mismatch`.

Nenhuma chave real ou parâmetro de estratégia foi alterado para implantar a correção.
