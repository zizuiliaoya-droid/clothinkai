"""U01 测试套件。

目录结构（按 NFR Requirements Q11=A / Q12=B / Q15=B）：
- unit/         单元测试（Domain / Service 算法层）
- integration/  集成测试（DB + Repository + Service）
- api/          API 端点测试（FastAPI TestClient）

每个测试函数运行在独立事务中，结束自动回滚（conftest.py session fixture）。
"""
