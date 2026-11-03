#ifndef CHARACTER_NAME_SCHEMA_H
#define CHARACTER_NAME_SCHEMA_H

#include <string>

class Database;

/// Fails closed unless the character-name ownership/generation structure is
/// present and exactly compatible. This never applies DDL or repairs metadata.
bool CheckCharacterNameSchema(Database& database, std::string& reason);
bool CheckCharacterNameOwnership(Database& database, std::string& reason);

#endif
