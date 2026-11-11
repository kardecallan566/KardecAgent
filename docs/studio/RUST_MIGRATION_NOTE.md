# Nota de evolução tecnológica — Rust (adiado)

**Decisão:** não migrar o núcleo para Rust neste momento.

O foco imediato é continuar evoluindo a implementação existente (core/agente em Python e Studio com Tauri + React + TypeScript) até alcançar um MVP funcional. O MVP deverá permitir validar a interface, conectar e executar o agente em tarefas reais, observar estados e aprovações e medir consumo de CPU, RAM e latência.

Após algumas versões e com dados de uso/benchmark, reavaliar uma migração **seletiva** para Rust. Migrar somente componentes em que Rust tenha benefício técnico demonstrável — por exemplo, concorrência/scheduler, serviços de sistema ou operações críticas de desempenho — preservando Python onde ele continuar adequado e produtivo.

## Critérios para reconsiderar Rust

- Gargalo identificado por profiling ou benchmark, não por suposição.
- Ganho mensurável de CPU, RAM, latência, segurança ou robustez.
- Fronteira de módulo clara e testes que preservem o comportamento.
- Custo de manutenção/interoperabilidade justificado pelo benefício.
- Migração incremental, sem reescrita total nem bloqueio da evolução do MVP.

**Princípio:** validar o produto primeiro; otimizar e migrar com evidências depois.
