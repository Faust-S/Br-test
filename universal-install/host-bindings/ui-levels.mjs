// Descriptive UI integration levels. Never pretends native windows are embedded.
export const LEVELS=Object.freeze(["I0","I1","I2","I3","I4","I5"]);
export function integrationObservation({discovered=false,dataAvailable=false,
  connected=false,capabilities=[],windowObservation=null,
  embeddedObservation=null,nativeObservation=null}={}){
 const operations=capabilities.filter(c=>c?.verified===true)
    .map(c=>c.name).filter(x=>typeof x==="string");
 const verified={
  I0:discovered===true,
  I1:dataAvailable===true,
  I2:connected===true&&operations.length>0,
  I3:windowObservation?.observed===true&&windowObservation?.native===true,
  I4:embeddedObservation?.verified===true&&embeddedObservation?.official===true,
  I5:nativeObservation?.verified===true&&nativeObservation?.contract===true
 };
 return {schema:"IWXA_UI_INTEGRATION/1.0.0",
  levels:LEVELS.map(level=>({level,verified:verified[level]})),
  highestVerified:LEVELS.filter(level=>verified[level]).at(-1)??null,
  verifiedOperations:operations};
}
