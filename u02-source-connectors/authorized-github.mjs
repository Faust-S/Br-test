import {createGitHubSourcePort} from './atlas-github.mjs';
// The embedding host supplies both its authorized observer and its fetch transport.
// No credential, token lookup, storage, or permission grant exists in U02.
export function createAuthorizedGitHubSourcePort({observeAuthorizedRepo,authorizedFetcher,...rest}={}){
 return createGitHubSourcePort({...rest,access:'authorized',
   observeRepository:observeAuthorizedRepo,fetcher:authorizedFetcher});
}
