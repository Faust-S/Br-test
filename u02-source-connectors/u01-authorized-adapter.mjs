import {observeAuthorizedGitHubRepo} from '../universal-install/host-bindings/github-source.mjs';
import {createAuthorizedGitHubSourcePort} from './authorized-github.mjs';

// The existing U01 observer owns discovery. The host owns credentials/HTTP.
export function createU01AuthorizedGitHubSourcePort({
  repository,branch,relativePath,fetcher,credentialProvider,signal,...options
}={}){
  if(typeof fetcher!=='function'||typeof credentialProvider!=='function')
    throw Error('GITHUB_HOST_CALLBACKS_REQUIRED');
  const observeAuthorizedRepo=repo=>observeAuthorizedGitHubRepo(repo,{
    fetcher,credentialProvider,signal
  });
  const authorizedFetcher=async (url,request={})=>{
    const token=await credentialProvider('github',repository);
    if(typeof token!=='string'||!token)
      return {ok:false,status:401};
    return fetcher(url,{...request,signal,
      headers:{...request.headers,Authorization:'Bearer '+token}});
  };
  return createAuthorizedGitHubSourcePort({repository,branch,relativePath,
    observeAuthorizedRepo,authorizedFetcher,...options});
}
