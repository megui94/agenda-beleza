USE defaultdb;

-- 👤 Tabela de utilizadores
CREATE TABLE IF NOT EXISTS Utilizador (
    Id INT AUTO_INCREMENT PRIMARY KEY,
    Password VARCHAR(255) NOT NULL,
    Nome VARCHAR(100) NOT NULL,
    Email VARCHAR(100) UNIQUE NOT NULL,
    Telefone VARCHAR(20),
    IsAdmin BOOLEAN DEFAULT FALSE,
    DataRegisto TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 💅 Tabela de serviços
CREATE TABLE IF NOT EXISTS Servicos (
    Id INT AUTO_INCREMENT PRIMARY KEY,
    Nome VARCHAR(100) NOT NULL,
    Descricao TEXT,
    Preco DECIMAL(10,2) DEFAULT 0.00,
    Duracao INT (45)
);

-- 📅 Tabela de marcações
CREATE TABLE IF NOT EXISTS Marcacoes (
    Id INT AUTO_INCREMENT PRIMARY KEY,
    DataHora DATETIME NOT NULL,
    Cliente_id INT NOT NULL,
    Servico_id INT NOT NULL,
    Estado VARCHAR(50) DEFAULT 'Pendente',
    Observacoes TEXT,
    FOREIGN KEY (Cliente_id) REFERENCES Utilizador(Id) ON DELETE CASCADE,
    FOREIGN KEY (Servico_id) REFERENCES Servicos(Id) ON DELETE CASCADE
);

-- Tabela de contato
CREATE TABLE IF NOT EXISTS MensagensContato (
    Id INT AUTO_INCREMENT PRIMARY KEY,
    Nome VARCHAR(100) NOT NULL,
    Email VARCHAR(150) NOT NULL,
    Assunto VARCHAR(200) NOT NULL,
    Mensagem TEXT NOT NULL,
    DataEnvio DATETIME DEFAULT CURRENT_TIMESTAMP
);

